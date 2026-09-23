#!/usr/bin/env python3
"""Daily push about stock that is expired or expiring soon — the reminder InvenTree lacks.

Part of inventree-pantry (https://github.com/setuun/inventree-pantry), MIT licensed.

Asks InvenTree which batches are expired or inside their warning window and sends ONE summary
to ntfy and/or a Matrix room. Run it once a day from a systemd timer or cron (deploy/ has
both). Runs anywhere that can reach InvenTree over HTTP; it needs nothing but Python 3.9+.

CONFIGURATION, two halves on purpose:
  * pantry.json ($PANTRY_CONFIG, default /etc/inventree-pantry/pantry.json) — `language` and
    `expiry` (the warning rule, shared with the app so both agree on what "soon" means)
  * the environment — everything secret or site-specific, typically from a systemd
    EnvironmentFile with mode 0600 (deploy/expiry-check.env.example):
      INVENTREE_URL        e.g. http://192.168.1.20:8080 (required)
      INVENTREE_TOKEN      an API token, or INVENTREE_USER + INVENTREE_PASSWORD
      PANTRY_CLICK_URL     link in the message, default <INVENTREE_URL>/vorrat/
      NTFY_URL, NTFY_TOPIC                         push via ntfy (optional)
      MATRIX_HOMESERVER, MATRIX_ROOM, MATRIX_TOKEN post into a Matrix room (optional)
      PANTRY_LANGUAGE      overrides `language` from pantry.json
    With no channel configured the summary goes to stdout, which cron mails to you.

WHY BASIC AUTH IS OFFERED AT ALL: InvenTree mints API tokens with a one-year expiry, so a
token-based watchdog goes silent a year later with no signal that it did. A password has no
expiry. Prefer the password for an unattended job, and keep the env file at 0600.

WHY CLIENT-SIDE FILTERING: InvenTree's stock API exposes an `expired` boolean filter, but
"expires within N days" has no stable documented query parameter across versions. Pulling the
items that HAVE an expiry date and comparing dates here is version-proof, and a household
inventory is a few hundred rows — the extra bytes are irrelevant.

Exit codes: 0 = ran (whether or not anything was pushed), 1 = InvenTree or a channel
unreachable. A non-zero exit fails the systemd unit, which surfaces in `systemctl --failed`.
"""

import base64
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html import escape

# Message texts. Deliberately worded without counted nouns ("Expired (3):" rather than
# "3 items expired"), so no language needs plural rules here. Adding a language: one more
# block, same keys. English is the fallback for anything missing.
TEXT = {
    'en': {'title': 'Pantry: {expired} expired, {soon} soon', 'expired': 'Expired ({n}):',
           'soon': 'Expiring soon ({n}):', 'in_days': 'in {n} d', 'open': 'Open pantry',
           'part': 'Part {pk}', 'date': '%d/%m'},
    'de': {'title': 'Vorrat: {expired} abgelaufen, {soon} bald', 'expired': 'Abgelaufen ({n}):',
           'soon': 'Läuft bald ab ({n}):', 'in_days': 'in {n} T.', 'open': 'Vorrat öffnen',
           'part': 'Artikel {pk}', 'date': '%d.%m.'},
    'ru': {'title': 'Запасы: просрочено {expired}, скоро {soon}', 'expired': 'Просрочено ({n}):',
           'soon': 'Скоро истекает ({n}):', 'in_days': 'через {n} дн.', 'open': 'Открыть запасы',
           'part': 'Товар {pk}', 'date': '%d.%m'},
    'zh': {'title': '储备：{expired} 件已过期，{soon} 件即将过期', 'expired': '已过期（{n}）：',
           'soon': '即将过期（{n}）：', 'in_days': '{n} 天后', 'open': '打开储备',
           'part': '物品 {pk}', 'date': '%m月%d日'},
}


def load_config():
    path = os.environ.get('PANTRY_CONFIG') or '/etc/inventree-pantry/pantry.json'
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


CONFIG = load_config()
EXPIRY = CONFIG.get('expiry') or {}
LANG = os.environ.get('PANTRY_LANGUAGE') or CONFIG.get('language') or 'en'
_T = dict(TEXT['en'], **TEXT.get(LANG, {}))

INVENTREE_URL = (os.environ.get('INVENTREE_URL') or '').rstrip('/')
API_TOKEN = os.environ.get('INVENTREE_TOKEN') or ''
API_USER = os.environ.get('INVENTREE_USER') or ''
API_PASSWORD = os.environ.get('INVENTREE_PASSWORD') or ''
CLICK_URL = os.environ.get('PANTRY_CLICK_URL') or (INVENTREE_URL + '/vorrat/')
NTFY_URL = (os.environ.get('NTFY_URL') or '').rstrip('/')
NTFY_TOPIC = os.environ.get('NTFY_TOPIC') or ''
MATRIX_HS = (os.environ.get('MATRIX_HOMESERVER') or '').rstrip('/')
MATRIX_ROOM = urllib.parse.quote(os.environ.get('MATRIX_ROOM') or '', safe='')
MATRIX_TOKEN = os.environ.get('MATRIX_TOKEN') or ''

# The warning window is a SHARE of each batch's shelf life, not a fixed number of days — see
# docs/design.md, and note that the app reads these same three numbers from the same file.
LEAD_FRACTION = float(EXPIRY.get('lead_fraction', 0.2))
LEAD_MIN_DAYS = int(EXPIRY.get('lead_min_days', 5))
LEAD_MAX_DAYS = int(EXPIRY.get('lead_max_days', 90))
TIMEOUT = 30


def lead_days(created, expiry, nominal):
    """How many days before `expiry` this batch should start being mentioned.

    Shelf life is the LARGER of the batch's own span and the kind's nominal durability
    (`default_expiry`, filled in from the category table in pantry.json). Using only the batch collapses
    during a stocktake — everything booked on one day has "shelf life" equal to "time left",
    so the warning always arrives at the same fraction of the remainder and UHT milk with 11
    days to go never surfaces. Using only the nominal value is wrong the other way round, for
    batteries dated years past their category default.
    """
    spans = [int(nominal or 0)]
    if created is not None:
        spans.append((expiry - created).days)
    shelf = max(spans)
    if shelf <= 0:
        return LEAD_MIN_DAYS
    return int(min(max(round(shelf * LEAD_FRACTION), LEAD_MIN_DAYS), LEAD_MAX_DAYS))

def auth_header():
    if API_TOKEN:
        return f"Token {API_TOKEN}"
    return "Basic " + base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()


def get_json(url):
    req = urllib.request.Request(url, headers={
        "Authorization": auth_header(),
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)


def fetch_stock():
    """All in-stock items, following DRF pagination."""
    items = []
    # in_stock=true skips consumed/shipped rows; limit=500 keeps the page count low.
    url = f"{INVENTREE_URL}/api/stock/?in_stock=true&part_detail=true&limit=500"
    while url:
        page = get_json(url)
        if isinstance(page, list):          # pagination disabled server-side
            items.extend(page)
            break
        items.extend(page.get("results", []))
        url = page.get("next")
    return items


def push_ntfy(title, body, expired):
    if not (NTFY_URL and NTFY_TOPIC):
        return
    req = urllib.request.Request(
        f"{NTFY_URL}/{NTFY_TOPIC}",
        data=body.encode("utf-8"),
        headers={
            "Title": title.encode("utf-8"),
            # High priority only when something is already expired.
            "Priority": "default" if not expired else "high",
            "Tags": "shopping" if not expired else "warning",
            "Click": CLICK_URL,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        resp.read()


def push_matrix(title, body):
    """Post the same summary into the Matrix room.

    Sent with BOTH a plain body and an HTML one: Element shows the formatted version, every
    other client and every notification preview falls back to the plain text, so the message is
    readable either way rather than only in the app it was written for.

    The transaction id is the current time in milliseconds. Matrix deduplicates on it, so a
    fixed value would mean the second push of the day silently vanishes.
    """
    if not (MATRIX_HS and MATRIX_ROOM and MATRIX_TOKEN):
        return

    plain = f"{title}\n{body}"
    html = ("<b>" + escape(title) + "</b><br><pre>" + escape(body) + "</pre>"
            + '<br><a href="' + escape(CLICK_URL) + '">' + escape(_T['open']) + '</a>')
    txn = str(int(time.time() * 1000))

    req = urllib.request.Request(
        f"{MATRIX_HS}/_matrix/client/v3/rooms/{MATRIX_ROOM}/send/m.room.message/{txn}",
        data=json.dumps({
            "msgtype": "m.text",
            "body": plain,
            "format": "org.matrix.custom.html",
            "formatted_body": html,
        }).encode("utf-8"),
        headers={"Authorization": f"Bearer {MATRIX_TOKEN}",
                 "Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        resp.read()


def main():
    if not INVENTREE_URL:
        print("ERROR: INVENTREE_URL is not set", file=sys.stderr)
        return 1
    today = datetime.date.today()

    try:
        stock = fetch_stock()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"ERROR: cannot reach InvenTree at {INVENTREE_URL}: {exc}", file=sys.stderr)
        return 1

    expired, soon = [], []
    for item in stock:
        raw = item.get("expiry_date")
        if not raw:
            continue
        try:
            expiry = datetime.date.fromisoformat(raw)
        except (TypeError, ValueError):
            continue
        # part_detail is only present when the API includes it; fall back to the pk so a
        # notification is never silently dropped just because a name is missing.
        created = None
        raw_created = (item.get("creation_date") or "")[:10]
        if raw_created:
            try:
                created = datetime.date.fromisoformat(raw_created)
            except (TypeError, ValueError):
                created = None

        name = ((item.get("part_detail") or {}).get("full_name")
                or _T['part'].format(pk=item.get('part')))
        qty = item.get("quantity")
        label = f"{name} (x{qty})" if qty else name

        if expiry < today:
            expired.append((expiry, label))
            continue

        lead = lead_days(created, expiry, (item.get("part_detail") or {}).get("default_expiry"))
        if (expiry - today).days <= lead:
            # The lead time is part of the line: "in 12 days" means something different for a
            # yoghurt than for a tin, and the number explains why this one is being mentioned.
            soon.append((expiry, f"{label} — " + _T['in_days'].format(n=(expiry - today).days)))

    if not expired and not soon:
        print("nothing expired or expiring soon")
        return 0

    lines = []
    # Day and month only: everything listed is within a few months of today anyway.
    if expired:
        lines.append(_T['expired'].format(n=len(expired)))
        lines += [f"  {d.strftime(_T['date'])} {n}" for d, n in sorted(expired)]
    if soon:
        lines.append(_T['soon'].format(n=len(soon)))
        lines += [f"  {d.strftime(_T['date'])} {n}" for d, n in sorted(soon)]
    body = "\n".join(lines)

    title = _T['title'].format(expired=len(expired), soon=len(soon))

    if not (NTFY_URL and NTFY_TOPIC) and not (MATRIX_HS and MATRIX_ROOM and MATRIX_TOKEN):
        print(title)
        print(body)
        return 0

    # Every configured channel is attempted, and a failure in one does not silence the other —
    # but any failure still fails the unit, so a channel that quietly stopped working shows up
    # in `systemctl --failed` rather than being discovered when something has already spoiled.
    failed = []
    for label, fn in (("ntfy", lambda: push_ntfy(title, body, expired)),
                      ("matrix", lambda: push_matrix(title, body))):
        try:
            fn()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            print(f"ERROR: cannot push to {label}: {exc}", file=sys.stderr)
            failed.append(label)

    print(f"pushed: {len(expired)} expired, {len(soon)} expiring soon"
          + (f" — FAILED: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
