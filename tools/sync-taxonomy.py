"""Reconcile the InvenTree category tree + stock locations from pantry.json.

Part of inventree-pantry (https://github.com/setuun/inventree-pantry), MIT licensed.

Run inside the inventree-server container, through Django's shell:

    docker exec -i -e PANTRY_CONFIG=/home/inventree/data/plugins/pantry.json inventree-server \
        sh -c 'cd /home/inventree/src/backend/InvenTree && python3 manage.py shell' \
        < tools/sync-taxonomy.py

$PANTRY_CONFIG defaults to /home/inventree/data/plugins/pantry.json — the copy the plugin
reads. Safe to run as often as you like: it creates what is missing and corrects what drifted,
and it never deletes anything.

WHY A SCRIPT AND NOT THE REST API
The previous version created both trees with two plain `uri` POSTs, which worked only as long
as every entry was top level and carried nothing but a name. Three of the fields that make the
taxonomy actually *useful* are foreign keys or need a pk that is not known until the same run
has created the row it points at — `parent`, `default_location` — and one of them
(`structural`) has to be applied to categories that already exist. Resolving that in Jinja is
possible and unreadable; `manage.py shell` has the models, so this uses them.

WHAT IT OWNS
  * existence of every declared stock location and part category, including one level of nesting
  * on categories: description, parent, `structural`, `default_location`, `default_keywords`
  * on locations: description, parent
  * the parameter template that carries a product's pack content (`pack_parameter`), without
    which the app cannot translate "one packet" into kilograms

WHAT IT DOES NOT TOUCH — on purpose
  * anything not listed. Removing an entry from pantry.json does NOT delete the category or
    location, and never its parts: deleting a category with stock behind it as a side effect of
    a config edit is far too sharp an edge. Delete it in the UI, deliberately.
  * `expiry_days`. That is not an InvenTree field at all — PartCategory has no expiry default.
    It is read by the Open Food Facts plugin as the `default_expiry` for parts it creates.

Prints one `CHANGED: …` line per actual modification and `NOCHANGE` if the state already
matched, so automation (Ansible's `changed_when`, for one) can tell. `ERROR: …` lines mean
something could not be applied.
"""

import json
import os

from django.core.exceptions import ValidationError

from part.models import PartCategory
from stock.models import StockLocation

CONFIG_PATH = os.environ.get('PANTRY_CONFIG') or '/home/inventree/data/plugins/pantry.json'

with open(CONFIG_PATH, encoding='utf-8') as _fh:
    CONFIG = json.load(_fh)

CATEGORIES = CONFIG.get('categories') or []
LOCATIONS = CONFIG.get('locations') or []
PACK_PARAMETER = CONFIG.get('pack_parameter') or 'Pack content'
# The template's description is shown in InvenTree's own UI, so it follows the household's
# language rather than this file's.
PACK_DESCRIPTION = CONFIG.get('pack_parameter_description') or \
    'Amount in one packet, in the unit of the article'

changes = []
errors = []


def reconcile(model, spec, parent, extra=None):
    """get-or-create `spec['name']` under `parent`, then drift managed fields back.

    Matching is on (name, parent), not name alone: the same leaf name under two parents is a
    legitimate tree, and matching on name only would silently adopt the wrong row.
    """
    name = spec['name']
    obj = model.objects.filter(name=name, parent=parent).first()
    created = False

    if obj is None:
        obj = model(name=name, parent=parent)
        created = True

    fields = {'description': spec.get('description', '')}
    fields.update(extra or {})

    dirty = []
    for field, wanted in fields.items():
        if getattr(obj, field, None) != wanted:
            dirty.append(f'{field}={wanted!r}')
            setattr(obj, field, wanted)

    if not created and not dirty:
        return obj

    try:
        # full_clean() is what enforces `structural`: InvenTree refuses to make a category
        # structural while parts are filed in it directly. Catching it here turns a traceback
        # into a sentence that says which category and why.
        obj.full_clean()
        obj.save()
    except ValidationError as exc:
        errors.append(f'{model.__name__} {name!r}: {exc.messages}')
        return None

    label = f'{model.__name__} {name!r}'
    changes.append(f'created {label}' if created else f'{label}: {", ".join(dirty)}')
    return obj


def sync(model, specs, extra_for=None):
    """Two passes: top level first, then the children — a child needs its parent's pk.

    One level of nesting is the whole recursion the taxonomy allows (see the defaults file),
    so two passes is not a simplification, it is the complete case.
    """
    by_name = {}

    for spec in specs:
        if spec.get('parent'):
            continue
        obj = reconcile(model, spec, None, (extra_for or (lambda s: {}))(spec))
        if obj is not None:
            by_name[spec['name']] = obj

    for spec in specs:
        parent_name = spec.get('parent')
        if not parent_name:
            continue
        parent = by_name.get(parent_name) or model.objects.filter(name=parent_name).first()
        if parent is None:
            errors.append(
                f'{model.__name__} {spec["name"]!r}: parent {parent_name!r} does not exist'
            )
            continue
        obj = reconcile(model, spec, parent, (extra_for or (lambda s: {}))(spec))
        if obj is not None:
            by_name[spec['name']] = obj

    return by_name


# --- locations first: categories point at them by name -----------------------
sync(StockLocation, LOCATIONS)


def category_extras(spec):
    """The three fields that carry the taxonomy's weight, resolved from names to objects."""
    location = None
    wanted = spec.get('default_location')
    if wanted:
        location = StockLocation.objects.filter(name=wanted).first()
        if location is None:
            errors.append(
                f'PartCategory {spec["name"]!r}: default_location {wanted!r} does not exist'
            )
    return {
        # Parts may not be filed directly in a structural category — this is what forces the
        # choice of a group instead of everything piling up in the parent ("food").
        'structural': bool(spec.get('structural', False)),
        'default_location': location,
        'default_keywords': spec.get('default_keywords', ''),
    }


sync(PartCategory, CATEGORIES, category_extras)


# --- the pack-content parameter template -------------------------------------
# A requirement measured in kilograms needs one number per product to be usable: how much is in
# one packet. The app writes it here when you book something in, so the template has to exist
# before the first scan — and it has to survive a restore, which is why it is created here and
# not by hand. No units on the template itself: the unit belongs to the part (kg or l), and a
# template pinned to kg would refuse the litre products.
try:
    from common.models import ParameterTemplate as _ParamTemplate
except ImportError:                       # InvenTree < 1.4 kept it under part.models
    from part.models import PartParameterTemplate as _ParamTemplate

_tpl = _ParamTemplate.objects.filter(name=PACK_PARAMETER).first()
if _tpl is None:
    # `model_type` scopes the template: InvenTree 1.4 allows parameters on more than parts, and
    # one left unscoped is not offered on a part at all.
    # ⚠️ On the MODEL this is a ContentType foreign key — only the REST layer takes the string
    # 'part'. Passing the string here raises ValueError and takes the whole sync down with it.
    from django.contrib.contenttypes.models import ContentType

    _ParamTemplate.objects.create(
        model_type=ContentType.objects.get(app_label='part', model='part'),
        name=PACK_PARAMETER,
        description=PACK_DESCRIPTION,
    )
    changes.append(f'parameter template {PACK_PARAMETER!r} created')

for e in errors:
    print(f'ERROR: {e}')

if changes:
    for c in changes:
        print(f'CHANGED: {c}')
elif not errors:
    print('NOCHANGE')
