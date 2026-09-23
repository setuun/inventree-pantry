# Installing inventree-pantry

**This guide is written to be carried out by an AI coding agent** (Claude Code, Codex,
Gemini CLI, …) working on the machine that runs InvenTree, or one that can reach it. A person
can follow it just as well; it is just more explicit than a human would need.

> **To the agent:** work through the steps in order. Every step says how to *check* it
> worked. Do the check and do not move on while it fails. Where a step is marked
> **ASK**, the decision belongs to the person you work for: ask, and do not guess. Where it is
> marked **CONFIRM**, say what you are about to do and wait for a yes. That covers anything
> that restarts InvenTree or changes its settings. Never print passwords or tokens back into
> the conversation. Read them from where they live, or ask the person to put them into a file.
> The app is vibe-coded (see README): if something here does not match what you find on the
> machine, report the mismatch instead of forcing it through.

## What you end up with

- the app at `https://<your-inventree>/vorrat/`, same origin as InvenTree
- the Open Food Facts plugin, active, filing scanned groceries into your categories
- your categories, storage locations and the pack-size parameter in InvenTree
- InvenTree's expiry tracking switched on
- optionally, a daily expiry push to ntfy and/or Matrix

## Step 0: find out what is there

Establish these facts before changing anything, and write them down for the person:

1. **How InvenTree runs.** The official Docker Compose setup is what this guide assumes
   (look for `docker compose ps` / `docker-compose ps` showing `inventree-server`,
   `inventree-worker`, `inventree-proxy`). A bare-metal install works too, but step 3 differs
   (use `deploy/nginx.snippet` or your web server's equivalent).
2. **The data directory**: the host path mounted at `/home/inventree/data` in
   `inventree-server` (`docker inspect inventree-server` → Mounts). Upstream calls it
   `INVENTREE_EXT_VOLUME`. It contains `static/`, `media/`, `plugins/`.
3. **The Caddyfile** the `inventree-proxy` container reads (`/etc/caddy/Caddyfile` inside,
   usually `./Caddyfile` next to the compose file).
4. **InvenTree's version**: `curl -s https://<host>/api/ | python3 -m json.tool` shows
   `version` and `apiVersion`. Tested with 1.4 / API 511. On a much older or newer
   version, tell the person and proceed carefully.
5. **Plugins enabled?** `INVENTREE_PLUGINS_ENABLED=True` in the environment of
   `inventree-server` (its `.env`). Without it the plugin in step 5 is never loaded.
6. **HTTPS?** The camera only works on a secure origin. Plain HTTP on a LAN IP means typing
   barcodes by hand. Fine for a test, not for daily use.
7. **Admin access**: a superuser account for steps 5 and 6 (API calls). Ask the person
   how you get the password; do not search for it.

Check: you can name all seven. If InvenTree is not running at all, stop here. Installing
InvenTree itself is out of scope ([its own guide](https://docs.inventree.org/en/stable/start/docker/)).

## Step 1: write pantry.json (ASK)

`pantry.json` is the one configuration file: the app, the plugin, the taxonomy script and
the watchdog all read it. Every key is explained in [docs/configuration.md](docs/configuration.md).

1. **ASK** which language the household mainly uses. Start from
   `examples/pantry.<lang>.json` (de, en, ru, zh), or from `pantry.en.json` for anything
   else. The app itself still follows each phone's language.
2. **ASK** about the household's storage places (kitchen, cellar, …) and whether the
   example categories fit. The examples are built for a pantry plus an emergency supply,
   following the German federal office for civil protection's (BBK) food groups, plus non-food.
   Edit `locations` and `categories` together with the person. Keep the rule from
   [docs/design.md](docs/design.md): categories say *what kind of thing* something is, never
   where it is (that is a location) or what it is for (that is a keyword).
3. **ASK** for a contact e-mail address for `openfoodfacts.contact`. Open Food Facts wants
   one in the User-Agent of every client.
4. Set `app.book_location` to the shelf new purchases usually go to, as a path
   (`Cellar/Emergency supply`), or leave it empty.

Check: `python3 tools/check-config.py pantry.json` exits 0. Warnings are allowed, but read them
to the person.

## Step 2: copy the files

```sh
sudo tools/install.sh <data-directory> pantry.json
```

This runs both checks, then copies the app to `<data-directory>/vorrat/` (with pantry.json as
`vorrat/config.json`) and the plugin plus pantry.json to `<data-directory>/plugins/`, owned
like the data directory. It deletes nothing.

Check: `ls <data-directory>/vorrat` shows `index.html`, `config.json`, `i18n/`, four
`manifest.*.webmanifest`; `ls <data-directory>/plugins` shows `openfoodfacts_barcode.py` and
`pantry.json`.

## Step 3: route /vorrat/ (CONFIRM before restarting)

**Caddy (the Docker setup):**

1. Paste `deploy/Caddyfile.snippet` into InvenTree's Caddyfile, *inside* the site block,
   next to the existing `handle_path /static/*` and `handle_path /media/*` blocks.
2. Give the proxy container the directory, read-only: put `deploy/docker-compose.override.yml`
   next to InvenTree's `docker-compose.yml`, or add its single volume line to the
   `inventree-proxy` service by hand. If the compose file does not use
   `${INVENTREE_EXT_VOLUME}`, write the host path from step 0 instead.
3. **CONFIRM**, then `docker compose up -d inventree-proxy` (recreates only the proxy).

**nginx / other:** `deploy/nginx.snippet`. The point is the same: `/vorrat/` must be served
by the same scheme, host and port as InvenTree's `/api/`.

Check: `curl -sI https://<host>/vorrat/` → `200` with `content-type: text/html`, and
`curl -s https://<host>/vorrat/config.json` returns your pantry.json, not HTML.

## Step 4: restart for the plugin (CONFIRM)

InvenTree builds its plugin registry at start-up, and the worker needs the plugin too (it
downloads product photos in the background):

```sh
docker compose restart inventree-server inventree-worker
```

This takes InvenTree offline for a minute or two. **CONFIRM** first.

Check: `curl -s -u <admin> https://<host>/api/plugins/openfoodfacts-barcode/` returns JSON
with `"key": "openfoodfacts-barcode"`. A 404 means the file is not in the plugin directory
the server reads, or plugins are disabled (step 0, item 5).

## Step 5: taxonomy, plugin activation, settings (CONFIRM)

These change InvenTree's database. Nothing is deleted. Say what will be created and
**CONFIRM**.

**Categories, locations, pack-size parameter**, from the pantry.json the plugin reads:

```sh
docker exec -i inventree-server sh -c \
  'cd /home/inventree/src/backend/InvenTree && python3 manage.py shell' \
  < tools/sync-taxonomy.py
```

Check: the output has `CHANGED:` lines on the first run, `NOCHANGE` on a second run, and no
`ERROR:` lines. An `ERROR` about `structural` means parts already sit directly in that
category; move them first.

**Activate the plugin and point it at a fallback category** (API calls as the admin; `$A` is
`-u admin:<password>` or `-H "Authorization: Token <token>"`):

```sh
H=https://<host>
curl -s $A -X PATCH -H 'Content-Type: application/json' -d '{"active": true}' \
  $H/api/plugins/openfoodfacts-barcode/activate/
# pk of the category unmapped groceries go to, e.g. "Other food" — a LEAF, not structural:
curl -s $A "$H/api/part/category/?name=Other%20food" | python3 -m json.tool | grep '"pk"'
curl -s $A -X PATCH -H 'Content-Type: application/json' -d '{"value": "<pk>"}' \
  $H/api/plugins/openfoodfacts-barcode/settings/CATEGORY/
# language of product names asked from Open Food Facts:
curl -s $A -X PATCH -H 'Content-Type: application/json' -d '{"value": "en"}' \
  $H/api/plugins/openfoodfacts-barcode/settings/LANGUAGE/
```

**Switch on expiry tracking.** InvenTree ships with it off, and without it there are no
best-before dates at all:

```sh
for kv in STOCK_ENABLE_EXPIRY=true STOCK_ALLOW_EXPIRED_SALE=true \
          STOCK_ALLOW_EXPIRED_BUILD=true STOCK_STALE_DAYS=7; do
  curl -s $A -X PATCH -H 'Content-Type: application/json' \
    -d "{\"value\": \"${kv#*=}\"}" $H/api/settings/global/${kv%%=*}/
done
```

(`ALLOW_EXPIRED_*`: yoghurt one day over its date must stay a judgement call, not become
unusable. `STOCK_STALE_DAYS` should match `expiry.warn_days`.)

Check: `curl -s $A $H/api/settings/global/STOCK_ENABLE_EXPIRY/` shows `"value": "True"`, and
the plugin shows `"active": true`.

## Step 6: accounts for the household (ASK)

Every person signs in with their own InvenTree account. **ASK** who needs one. A regular
user needs these permissions, best given through a group (InvenTree admin → Users → Groups).
Nothing administrative is on the list, on purpose:

| Model | Permissions | Why |
|---|---|---|
| part.part | view, add, change, delete | the product catalogue; delete so a mistyped product can go |
| part.partcategory | view, add, change | categories (no delete: a category with parts in it is a foot-gun) |
| stock.stockitem | view, add, change, delete | book in, use up, correct |
| stock.stocklocation | view, add, change | a new shelf or box |
| stock.stockitemtracking | view | history, written by the app itself |
| common.attachment | view, add, change, delete | photos, receipts |
| common.parameter | view, add, change, delete | the pack size per product |
| common.parametertemplate | view | reading the pack-size template |
| common.barcodescanresult | view, add | scanning |
| common.inventreeusersetting | view, add, change | a user's own preferences |

Check: sign in as that user at `https://<host>/vorrat/`. The stock list appears (empty on a
fresh install), and scanning or typing a known grocery barcode, e.g. `3017620422003`, creates a
product. If a scan says "unknown barcode" for a common product, the plugin is not active or
cannot reach `world.openfoodfacts.org`.

## Step 7: phones

On each phone (Chrome on Android recommended, it has the native barcode scanner): open
`https://<host>/vorrat/`, sign in once, then browser menu → *Add to home screen*. The app
follows the phone's language; the two letters in the header switch it.

## Step 8 (optional): daily expiry push (ASK)

**ASK** whether the household wants a daily message, and where: an [ntfy](https://ntfy.sh)
topic, a Matrix room, or both. Then, on any Linux machine that reaches InvenTree (the
InvenTree host itself is fine):

```sh
sudo install -m 0755 tools/expiry-check.py /usr/local/bin/inventree-pantry-expiry-check
sudo install -d /etc/inventree-pantry
sudo install -m 0644 pantry.json /etc/inventree-pantry/pantry.json
sudo install -m 0600 deploy/expiry-check.env.example /etc/inventree-pantry/expiry-check.env
# have the PERSON fill in /etc/inventree-pantry/expiry-check.env (it holds a password)
sudo install -m 0644 deploy/expiry-check.service /etc/systemd/system/inventree-pantry-expiry.service
sudo install -m 0644 deploy/expiry-check.timer /etc/systemd/system/inventree-pantry-expiry.timer
sudo systemctl daemon-reload
sudo systemctl enable --now inventree-pantry-expiry.timer
```

Check: `sudo systemctl start inventree-pantry-expiry.service && journalctl -u
inventree-pantry-expiry -n 20` shows "nothing expired or expiring soon" or "pushed: …". A
test with something already expired should arrive on the phone.

## Updating

Pull the new version, read `CHANGELOG.md`, and run `tools/install.sh` again with the same
pantry.json. The app needs no restart (reload the page on the phone). The plugin does: step 4.
If pantry.json changed, run the taxonomy script again (step 5, first block).

## Removing

Delete `<data-directory>/vorrat/` and `plugins/openfoodfacts_barcode.py` + `plugins/pantry.json`,
remove the Caddy route and the volume, restart. Your data stays in InvenTree untouched. The
products, batches and categories are ordinary InvenTree records.
