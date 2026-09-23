#!/usr/bin/env python3
"""Check a pantry.json before it is deployed.

    python3 tools/check-config.py examples/pantry.en.json [more.json …]

Every name in the file points at another name in the same file — a category's parent and
default location, the Open Food Facts map's target categories, the app's booking shelf. A typo
in any of them fails *quietly* at run time: the taxonomy script reports an ERROR line that
nobody reads, the plugin files a scanned product into the fallback category, the app simply
does not preselect the shelf. So this looks at the references, and at the numbers the expiry
rule is built from. docs/configuration.md describes every key.

Exit code 1 and a list of findings on failure. Warnings (an unset contact address, a language
without translations) are printed but do not fail — the examples carry a placeholder address
on purpose.
"""

import json
import sys

LANGUAGES_WITH_TEXT = {'de', 'en', 'ru', 'zh'}   # expiry-check.py has messages for these


def check(path):
    problems, warnings = [], []
    try:
        with open(path, encoding='utf-8') as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as exc:
        return [f'cannot read: {exc}'], []
    if not isinstance(cfg, dict):
        return ['top level must be an object'], []

    locations = cfg.get('locations') or []
    categories = cfg.get('categories') or []
    loc_names = [loc.get('name') for loc in locations]
    cat_names = [cat.get('name') for cat in categories]

    for kind, names in (('location', loc_names), ('category', cat_names)):
        for n in {n for n in names if names.count(n) > 1}:
            problems.append(f'{kind} {n!r} is declared twice')
        if None in names or '' in names:
            problems.append(f'a {kind} has no name')

    # Paths as InvenTree writes them: "Cellar/Emergency supply".
    loc_paths = set()
    for loc in locations:
        parent = loc.get('parent')
        if parent and parent not in loc_names:
            problems.append(f'location {loc.get("name")!r}: parent {parent!r} is not a location')
        loc_paths.add(f'{parent}/{loc.get("name")}' if parent else loc.get('name'))
        if parent and any(p.get('name') == parent and p.get('parent') for p in locations):
            problems.append(f'location {loc.get("name")!r}: only one level of nesting is supported')

    structural = {c.get('name') for c in categories if c.get('structural')}
    for cat in categories:
        name = cat.get('name')
        parent = cat.get('parent')
        if parent and parent not in cat_names:
            problems.append(f'category {name!r}: parent {parent!r} is not a category')
        if parent and any(c.get('name') == parent and c.get('parent') for c in categories):
            problems.append(f'category {name!r}: only one level of nesting is supported')
        where = cat.get('default_location')
        if where and where not in loc_names:
            problems.append(f'category {name!r}: default_location {where!r} is not a location')
        days = cat.get('expiry_days', 0)
        if not isinstance(days, int) or days < 0:
            problems.append(f'category {name!r}: expiry_days must be a whole number ≥ 0')

    off = cfg.get('openfoodfacts') or {}
    for entry in off.get('category_map') or []:
        target = entry.get('category')
        if target not in cat_names:
            problems.append(f'openfoodfacts.category_map: {target!r} is not a category')
        elif target in structural:
            problems.append(f'openfoodfacts.category_map: {target!r} is structural and '
                            'cannot hold parts')
        if not entry.get('tokens'):
            problems.append(f'openfoodfacts.category_map: {target!r} has no tokens')
    fallback = off.get('fallback_location')
    if fallback and fallback not in loc_names:
        problems.append(f'openfoodfacts.fallback_location {fallback!r} is not a location')
    if off.get('contact') in (None, '', 'you@example.org'):
        warnings.append('openfoodfacts.contact is not set — Open Food Facts asks every client '
                        'for a contact address in its User-Agent')

    book = (cfg.get('app') or {}).get('book_location')
    if book and book not in loc_paths and book not in loc_names:
        problems.append(f'app.book_location {book!r} is neither a location nor a location path '
                        f'(known paths: {", ".join(sorted(loc_paths))})')

    exp = cfg.get('expiry') or {}
    lo, hi = exp.get('lead_min_days', 5), exp.get('lead_max_days', 90)
    frac = exp.get('lead_fraction', 0.2)
    if not (isinstance(frac, (int, float)) and 0 < frac < 1):
        problems.append('expiry.lead_fraction must be between 0 and 1')
    if not (isinstance(lo, int) and isinstance(hi, int) and 0 <= lo <= hi):
        problems.append('expiry.lead_min_days and lead_max_days must be whole numbers, min ≤ max')

    lang = cfg.get('language')
    if lang and lang not in LANGUAGES_WITH_TEXT:
        warnings.append(f'language {lang!r}: the app falls back to English unless '
                        f'app/i18n/{lang}.json exists, and expiry-check.py has no messages '
                        'for it (see TRANSLATING.md)')
    return problems, warnings


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    failed = 0
    for path in sys.argv[1:]:
        problems, warnings = check(path)
        if problems or warnings:
            print(f'{path}:', file=sys.stderr)
        for p in problems:
            print(f'  - {p}', file=sys.stderr)
        for w in warnings:
            print(f'  ~ warning: {w}', file=sys.stderr)
        failed += bool(problems)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
