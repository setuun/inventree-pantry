"""Open Food Facts barcode lookup for InvenTree.

Part of inventree-pantry (https://github.com/setuun/inventree-pantry), MIT licensed.
Install: copy this file and pantry.json into InvenTree's plugin directory, restart the server
and the worker, activate the plugin. INSTALL.md has the details.

CONFIGURATION lives in pantry.json (the same file the app and the taxonomy script read):
category map, label keywords, expiry defaults per category, fallback location, the name of
the pack-content parameter and a contact address for Open Food Facts. The file is read on
every scan, so editing it needs no restart. Where it is looked for: $PANTRY_CONFIG, else
pantry.json next to this file.

WHY THIS EXISTS
InvenTree can bind an arbitrary supermarket EAN to a Part ("Link Barcode"), so the second
and every later scan of a product resolves instantly. What it cannot do is tell you what a
*brand-new* barcode is — you type the product name by hand once. grocy gets that for free
from Open Food Facts; this plugin closes the gap, which was the last functional argument for
grocy over InvenTree (see docs/design.md).

HOW IT FITS INTO THE SCAN CHAIN
`plugin/base/barcodes/api.py` asks every BARCODE-mixin plugin in registry order and takes the
first non-None result. The builtin `InvenTreeBarcode` plugin runs first and already resolves
(a) internal JSON/short barcodes and (b) *linked* external barcodes via
`model.lookup_barcode(barcode_hash)`. So by the time this plugin is asked, the barcode is
genuinely unknown. We re-check `lookup_barcode` anyway — registry order is not a contract we
want to depend on, and returning a wrong Part is far worse than doing nothing.

WHAT IT DOES
1. Ignores anything that is not a valid GTIN-8/12/13/14 (checksum verified). A mis-scan or a
   random QR code must never reach the network or create a Part.
2. Asks Open Food Facts for the product name.
3. Creates a Part in the configured category, links the barcode to it, and returns it — so the
   scan that discovers the product also files it. Every later scan is then a pure local hit.

Step 3 is gated behind the AUTO_CREATE setting: turn it off and the plugin reports the name it
found without writing anything, which is the safer mode if mis-scans are frequent.

WHAT v1.1 ADDED, AND WHY
v1.0 created a bare Part: no category beyond a fixed fallback, no expiry, no location, no
keywords — so every scan still ended in a form. The four fields below are the difference
between "the scan filed it" and "the scan started the filing", and three of them come for free
from data OFF already returns:
  * CATEGORY   — mapped from OFF's `categories_tags` onto our own tree (openfoodfacts.category_map)
  * EXPIRY     — `default_expiry` in days, per category, from `expiry_days` in pantry.json.
                 PartCategory has no expiry field of its own, so this table is the only place
                 that knowledge can live. It makes InvenTree pre-fill the MHD on every stock
                 item booked against the part, which is the single biggest saving in the whole
                 pantry workflow: a date typed once per product instead of once per jar.
  * LOCATION   — the category's `default_location`, else the plugin's configured fallback
  * KEYWORDS   — OFF's `labels_tags` mapped to the household's words (organic → "bio"). `keywords` is the
                 flat second dimension the category tree deliberately cannot carry; Part has no
                 `tags` field in API 511.
All four are DEFAULTS on the part, not facts about the stock — every one stays editable, and
nothing here overwrites a part that already exists.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from django.core.files.base import ContentFile
from django.utils.translation import gettext_lazy as _

import structlog

from InvenTree.tasks import offload_task
from part.models import Part, PartCategory
from plugin import InvenTreePlugin
from plugin.mixins import BarcodeMixin, SettingsMixin
from stock.models import StockLocation

logger = structlog.get_logger('inventree')

PLUGIN_VERSION = '2.0.0'


def load_config() -> dict:
    """pantry.json, or an empty dict. Read on every call, so an edit needs no restart.

    A broken or missing file is logged and treated as "no configuration": the plugin then
    still names products and links barcodes, it only files everything into the fallback
    category. A scan must never fail over a config file.
    """
    path = os.environ.get('PANTRY_CONFIG') or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'pantry.json')
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning('pantry.json at %s is unreadable: %s', path, exc)
        return {}


def off_config() -> dict:
    """The `openfoodfacts` block, plus the two tables it shares with the rest of the file.

    The expiry table is derived from `categories` rather than kept as its own list: it is the
    same list the taxonomy sync uses, so the expiry defaults cannot drift away from the tree
    they describe.
    """
    cfg = load_config()
    off = cfg.get('openfoodfacts') or {}
    return {
        'category_map': off.get('category_map') or [],
        'label_keywords': off.get('label_keywords') or {},
        'keywords': off.get('keywords') or [],
        # Used only when the matched category has no default_location of its own.
        'fallback_location': off.get('fallback_location') or '',
        'contact': off.get('contact') or '',
        'image_timeout': int(off.get('image_timeout') or 20),
        'image_max_kb': int(off.get('image_max_kb') or 2048),
        'expiry_days': {
            c['name']: int(c.get('expiry_days') or 0)
            for c in cfg.get('categories') or [] if c.get('name')
        },
        'pack_parameter': cfg.get('pack_parameter') or 'Pack content',
    }


def user_agent(contact: str) -> str:
    """Open Food Facts asks every client for an identifying User-Agent and throttles or blocks
    generic ones. Keep it honest and contactable: set `openfoodfacts.contact` in pantry.json."""
    who = f'self-hosted; {contact}' if contact else 'self-hosted'
    return f'inventree-pantry-OFF-plugin/{PLUGIN_VERSION} ({who})'


# Image fetching. The cap and the type allow-list are not paranoia about Open Food Facts but
# about what a Part row is allowed to become: this runs inside an interactive scan, so a slow
# or enormous response has to be cut off rather than waited out. OFF's front_*.400 renditions
# are ~10-30 KB, so the default cap is generous by two orders of magnitude.
IMAGE_TYPES = ('image/jpeg', 'image/png', 'image/webp')


def fetch_product_image(part_pk: int, url: str, code: str) -> bool:
    """Download the OFF product photo onto a part. Runs on the WORKER, not in the scan.

    WHY OFFLOADED, measured rather than assumed (2026-09-14): the OFF *lookup* answers in
    ~0.1 s, but images.openfoodfacts.org took **9 seconds** to hand over a 12 KB JPEG. Doing
    that inline made a scan take 7 s, which is the difference between a tool you use at the
    shelf and one you stop using. The part, its category, its MHD default and its barcode link
    are all committed before this is queued, so the scan is already complete and correct when
    the picture is still missing; it appears a few seconds later and the page polls for it.

    Module-level on purpose: offload_task serialises `module.function`, so a bound method
    cannot be queued. If the worker cannot import this module, InvenTree falls back to running
    it synchronously — slower, never broken.

    Never raises. A picture is a nicety; everything here is bounded: its own timeout, a hard
    byte cap enforced by reading cap+1 and rejecting anything that fills it, and a
    Content-Type allow-list so a redirect to an HTML error page cannot be stored as a photo.

    The file is named after the barcode, not after the URL: re-scanning a product whose part
    was deleted would otherwise leave Django deduplicating `front_en.jpg` into
    `front_en_K7a2p.jpg` forever.
    """
    part = Part.objects.filter(pk=part_pk).first()

    if part is None or part.image:
        return False

    cfg = off_config()
    max_bytes = cfg['image_max_kb'] * 1024

    try:
        req = urllib.request.Request(url, headers={'User-Agent': user_agent(cfg['contact'])})
        with urllib.request.urlopen(req, timeout=cfg['image_timeout']) as resp:
            ctype = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            if ctype not in IMAGE_TYPES:
                logger.warning('OFF image for %s has type %r, skipped', code, ctype)
                return False
            data = resp.read(max_bytes + 1)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        logger.warning('OFF image download failed for %s: %s', code, exc)
        return False

    if not data or len(data) > max_bytes:
        logger.warning('OFF image for %s missing or over the cap, skipped', code)
        return False

    suffix = {'image/png': 'png', 'image/webp': 'webp'}.get(ctype, 'jpg')

    try:
        part.image.save(f'{code}.{suffix}', ContentFile(data), save=True)
    except Exception as exc:
        logger.warning('OFF image could not be stored for %s: %s', code, exc)
        return False

    logger.info('OFF image attached to part %s from %s', part_pk, url)
    return True


def gtin_is_valid(code: str) -> bool:
    """True if `code` is a syntactically valid GTIN-8/12/13/14 (check digit verified).

    One algorithm covers all four lengths: walk right-to-left from the digit *before* the
    check digit, weighting 3,1,3,1,... The check digit makes the weighted total a multiple
    of 10. Rejecting bad checksums is what keeps mis-scans out of the database.
    """
    if not re.fullmatch(r'\d{8}|\d{12}|\d{13}|\d{14}', code):
        return False

    body, check = code[:-1], int(code[-1])
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)

    return (10 - total % 10) % 10 == check


class OpenFoodFactsBarcodePlugin(SettingsMixin, BarcodeMixin, InvenTreePlugin):
    """Resolve unknown grocery barcodes via Open Food Facts."""

    NAME = 'OpenFoodFactsBarcode'
    SLUG = 'openfoodfacts-barcode'
    TITLE = _('Open Food Facts barcode lookup')
    DESCRIPTION = _(
        'Look up unknown grocery barcodes in the Open Food Facts database and optionally '
        'create the matching part automatically.'
    )
    VERSION = PLUGIN_VERSION
    AUTHOR = 'inventree-pantry'
    WEBSITE = 'https://github.com/setuun/inventree-pantry'

    SETTINGS = {
        'AUTO_CREATE': {
            'name': _('Create parts automatically'),
            'description': _(
                'Create a new part (and link the barcode to it) when Open Food Facts '
                'recognises an unknown barcode. Turn off to only report the name.'
            ),
            'validator': bool,
            'default': True,
        },
        'CATEGORY': {
            'name': _('Target category'),
            'description': _('Category new grocery parts are filed under.'),
            'model': 'part.partcategory',
        },
        'TIMEOUT': {
            'name': _('Lookup timeout (seconds)'),
            'description': _(
                'Give up on Open Food Facts after this long. A barcode scan is interactive — '
                'keep this short so a slow lookup cannot hang the scanner.'
            ),
            'validator': [int, {'min_value': 1, 'max_value': 30}],
            'default': 6,
        },
        'FETCH_IMAGE': {
            'name': _('Fetch product image'),
            'description': _(
                'Download the product photo from Open Food Facts and attach it to the new '
                'part. Runs on the background worker, so it never delays a scan.'
            ),
            'validator': bool,
            'default': True,
        },
        'LANGUAGE': {
            'name': _('Preferred product-name language'),
            'description': _(
                'Two-letter code. Open Food Facts often carries several localised names; '
                'this one is preferred when present.'
            ),
            'default': 'en',
        },
    }

    # --- helpers -------------------------------------------------------------

    def _lookup(self, code: str, timeout: int, contact: str) -> dict | None:
        """Query Open Food Facts. Returns the product dict, or None."""
        fields = ','.join([
            'product_name',
            f'product_name_{self.get_setting("LANGUAGE") or "en"}',
            'generic_name',
            'brands',
            'quantity',
            'categories_tags',
            'labels_tags',
            'image_front_url',
            'image_url',
        ])
        url = (
            'https://world.openfoodfacts.org/api/v2/product/'
            f'{urllib.parse.quote(code)}.json?fields={urllib.parse.quote(fields)}'
        )

        req = urllib.request.Request(url, headers={'User-Agent': user_agent(contact)})

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.load(resp)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            # A lookup failure is NOT an error for the scan as a whole: returning None just
            # means "I have nothing to add", and InvenTree reports the barcode as unknown as
            # it would have anyway. Never raise — an exception here would surface to the user
            # as a broken scan instead of an unrecognised one.
            logger.warning('OFF lookup failed for %s: %s', code, exc)
            return None

        # status 1 = found, 0 = not in the database.
        if payload.get('status') != 1:
            return None

        return payload.get('product') or None

    def _build_name(self, product: dict) -> tuple[str, str]:
        """Return (name, description) for a Part, from an OFF product dict."""
        lang = self.get_setting('LANGUAGE') or 'en'

        name = (
            product.get(f'product_name_{lang}')
            or product.get('product_name')
            or product.get('generic_name')
            or ''
        ).strip()

        brands = (product.get('brands') or '').split(',')[0].strip()
        quantity = (product.get('quantity') or '').strip()

        # Brand in the name keeps "Haselnusscreme" from colliding across three vendors.
        if brands and brands.lower() not in name.lower():
            name = f'{name} ({brands})' if name else brands

        # Part.name is CharField(max_length=100).
        name = name[:100]

        description = ' · '.join(p for p in (brands, quantity) if p)[:250]

        return name, description

    # "500 g", "1,5 l", "4.5kg" -> the number in the base unit (kg or l). Anything else
    # ("6 x 1 l", "1 packet", empty) gives None: no figure is better than a wrong one, because
    # whether a requirement counts as met hangs on this number.
    _PACK_RE = re.compile(r'^\s*(\d+(?:[.,]\d+)?)\s*(kg|g|l|ml|cl)\s*$', re.IGNORECASE)
    _PACK_FACTOR = {'kg': 1.0, 'g': 0.001, 'l': 1.0, 'ml': 0.001, 'cl': 0.01}

    @classmethod
    def _pack_content(cls, quantity: str) -> float | None:
        """How much is in one packet, in the part's base unit. None when OFF is unclear."""
        match = cls._PACK_RE.match(quantity or '')
        if not match:
            return None
        value = float(match.group(1).replace(',', '.')) * cls._PACK_FACTOR[match.group(2).lower()]
        return round(value, 4) or None

    def _store_pack_content(self, part, quantity: str, parameter: str) -> None:
        """Record the packet size as a number, so the app can turn packets into kilograms.

        Deliberately does NOT touch `part.units`: whether this article is counted or measured is
        decided by the requirement it gets assigned to, not by the packet. Storing the number is
        safe either way — a counted requirement simply ignores it.
        """
        content = self._pack_content(quantity)
        if not content:
            return
        try:
            from common.models import Parameter, ParameterTemplate
            from django.contrib.contenttypes.models import ContentType

            template = ParameterTemplate.objects.filter(name=parameter).first()
            if template is None:
                return
            # ⚠️ ContentType instance, not the string 'part' — the string is the REST spelling
            # and raises ValueError on the model.
            Parameter.objects.get_or_create(
                model_type=ContentType.objects.get(app_label='part', model='part'),
                model_id=part.pk, template=template,
                defaults={'data': str(content)},
            )
        except Exception as exc:                      # never fail a scan over a nice-to-have
            logger.warning('OFF plugin could not store pack content for %s: %s', part.pk, exc)

    def _map_category(self, product: dict, category_map: list) -> str | None:
        """Pick one of our category names from OFF's `categories_tags`. None if nothing fits.

        OFF returns its tags broad -> narrow (`en:plant-based-foods` long before
        `en:canned-vegetables`). We walk them NARROW-FIRST, so the most specific statement OFF
        makes about a product is the one that decides; within a single tag, category_map order
        breaks the tie. That ordering is the whole reason canned fish lands in the fish group
        rather than in "other food".
        """
        for tag in reversed(product.get('categories_tags') or []):
            bare = tag.split(':', 1)[-1]
            for entry in category_map:
                for token in entry.get('tokens') or []:
                    if token in bare:
                        return entry['category']

        return None

    def _build_keywords(self, product: dict, cfg: dict) -> str:
        """The configured base keywords plus any OFF label we have a word for."""
        words = list(cfg['keywords'])

        for tag in product.get('labels_tags') or []:
            word = cfg['label_keywords'].get(tag.split(':', 1)[-1])
            if word and word not in words:
                words.append(word)

        # Part.keywords is CharField(max_length=250).
        return ', '.join(words)[:250]

    # --- BarcodeMixin --------------------------------------------------------

    def scan(self, barcode_data, user, **kwargs):
        """Called by /api/barcode/ for every scan. Return None to stay out of the way."""
        # Only ever act on a bare numeric GTIN. Dicts (internal JSON barcodes), QR payloads
        # and anything else belong to other plugins.
        if not isinstance(barcode_data, str):
            return None

        code = barcode_data.strip()

        if not gtin_is_valid(code):
            return None

        # Defensive: if this barcode is already linked to a Part, say nothing and let the
        # builtin plugin's answer stand. Guards against registry-order changes.
        from InvenTree.helpers import hash_barcode

        if Part.lookup_barcode(hash_barcode(code)) is not None:
            return None

        try:
            timeout = int(self.get_setting('TIMEOUT') or 6)
        except (TypeError, ValueError):
            timeout = 6

        cfg = off_config()
        product = self._lookup(code, timeout, cfg['contact'])

        if not product:
            return None

        name, description = self._build_name(product)

        if not name:
            # Open Food Facts has the barcode but no usable name (a stub entry). Nothing to
            # offer, so behave as if we never matched.
            return None

        if not self.get_setting('AUTO_CREATE'):
            # Report-only mode: no database write. 'success' is what the UI surfaces.
            return {
                'success': _('Open Food Facts: {name}').format(name=name),
                'openfoodfacts': {
                    'name': name,
                    'description': description,
                    'barcode': code,
                    'category': self._map_category(product, cfg['category_map']),
                },
            }

        # Mapped category first, the configured CATEGORY setting only as the safety net. That
        # setting must point at a LEAF ("other food"), not at a structural parent ("food"),
        # because a structural category refuses to hold parts at all.
        category = None
        mapped = self._map_category(product, cfg['category_map'])

        if mapped:
            category = PartCategory.objects.filter(name=mapped, structural=False).first()

        if category is None:
            category_pk = self.get_setting('CATEGORY')
            if category_pk:
                category = PartCategory.objects.filter(pk=category_pk).first()

        # The category's own default_location wins: it is the per-group answer the taxonomy
        # already gives, and the fallback location only covers a part that matched nothing.
        location = category.default_location if category else None

        if location is None and cfg['fallback_location']:
            location = StockLocation.objects.filter(name=cfg['fallback_location']).first()

        try:
            part = Part.objects.create(
                name=name,
                description=description or name,
                category=category,
                # The three defaults that keep the scan from ending in a form. default_expiry
                # is in DAYS: InvenTree turns it into a concrete date on each stock item, so
                # the MHD is typed once per product instead of once per jar.
                default_expiry=cfg['expiry_days'].get(category.name, 0) if category else 0,
                default_location=location,
                keywords=self._build_keywords(product, cfg),
                # A grocery is bought, consumed, and stock-tracked; it is not a component of
                # an assembly and we do not sell it.
                purchaseable=True,
                salable=False,
                component=False,
                assembly=False,
                active=True,
            )
            # raise_error=False: a race that linked this barcode between our lookup above and
            # here should not turn into a 500 on the user's scanner.
            part.assign_barcode(barcode_data=code, raise_error=False)
        except Exception as exc:
            logger.error('OFF plugin could not create part for %s: %s', code, exc)
            return None

        # Queued last, and deliberately after assign_barcode: the scan is already complete and
        # correct at this point, so the picture can take as long as it likes on the worker.
        if self.get_setting('FETCH_IMAGE'):
            image_url = product.get('image_front_url') or product.get('image_url')
            if image_url:
                offload_task(fetch_product_image, part.pk, image_url, code)

        self._store_pack_content(part, (product.get('quantity') or '').strip(),
                                 cfg['pack_parameter'])

        logger.info('OFF plugin created part %s (%s) from barcode %s', part.pk, name, code)

        return {
            **{Part.barcode_model_type(): part.format_matched_response(user=user)},
            'success': _('Created "{name}" from Open Food Facts').format(name=name),
        }
