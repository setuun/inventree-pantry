# Design notes

Why the project is shaped the way it is. Most of these were learned the hard way, in one real
household, between July and September 2026. The code comments carry the details.

## Why InvenTree, and not grocy

grocy is the obvious pantry app, and it gets two things for free that InvenTree lacks: a
product lookup for unknown barcodes and expiry reminders. InvenTree won anyway: it is actively
developed Python/Django, has proper stock history, part images, template/variant parts, a
plugin system, and it keeps working for the non-food half of a household (renovation material,
tools). The two missing pieces are what this repository adds: the Open Food Facts plugin and
the expiry watchdog. The third piece, the app, exists because of how InvenTree feels in a
kitchen.

## One static page on InvenTree's own origin

The household needs three verbs: book in, use up, count. InvenTree's official app puts each of
them several screens deep behind Part → Stock Item → Adjust. That is correct for a warehouse
and it is the reason the pantry stopped being maintained. So: one page, three buttons.

It is served **from InvenTree's own web server, on the same origin as the API**. That removes
every CORS preflight and every `CORS_ALLOWED_ORIGINS` entry, and it lets the page trade an
existing `/web/` session for an API token without the user typing anything. Any other origin
reintroduces CORS *and* a token to save nothing. The cost is one route and one read-only mount.

**No build step, no framework, no CDN.** InvenTree's proxy often has no outbound internet in
the serving path, and a household app should still open in ten years. Scanning uses the
browser's native `BarcodeDetector` (Chrome on Android); where it is missing, the page says so and
takes a typed barcode.

**One optional polyfill, on the same terms.** Safari has no `BarcodeDetector`, and every browser
on an iPhone is Safari underneath, so a household of iPhones could only type. `tools/fetch-scanner.sh`
puts a polyfill into `app/vendor/`: ZXing-C++ compiled to WebAssembly (the `barcode-detector`
package), pinned by version and checksum. It keeps every rule above: it is served from InvenTree's
own origin like the page (never a CDN; the polyfill's default of fetching its `.wasm` from
jsDelivr is overridden), it needs no build, and it is loaded lazily, only when the scan screen
opens on a browser without a native detector, so a phone that has one never downloads it.
Without the files the page behaves exactly as before. It is opt-in because it is 1.1 MB of
someone else's binary, not because it is risky to leave out.

**It is a front end, not a second source of truth.** It holds no state beyond the session and
every action is one documented API call. `/vorrat/` itself is readable without signing in, which
is fine: it contains no data, and the API answers 401 without a token.

## Authentication: one login per device, then a year of silence

The page first reused the browser session. That was wrong in practice: the session belongs to
the *browser*, so "signed in to the app" meant "signed in to `/web/` recently, in this same
browser", and it expired from under an app on the home screen. Now it mints a token with
`GET /api/user/me/token/?name=<device>` and keeps it in `localStorage`.

Three rungs on start, cheapest first: a stored token; else an existing `/web/` session traded
silently for a token; else a login card. A 401 anywhere drops the token and shows the card. That
is also how the token's one-year expiry surfaces: as one re-login, not a mystery. Minting is
idempotent per name (verified), and DRF's token authentication is CSRF-exempt, so no
`csrftoken` dance. The password is sent once and never stored. Each device is its own revocable
token row in InvenTree.

Pictures under `/media/` sit behind InvenTree's `forward_auth`: 401 without auth, 401 with Basic,
200 with the token header. An `<img src>` cannot send a header, so the page fetches images with
the token and hands the `<img>` an object URL.

## The category tree carries one dimension

InvenTree's categories are a strict single-parent tree, so they can express exactly one thing.
Every product that is at once a *kind* (tinned food), in a *place* (cellar) and for a *purpose*
(emergency supply) turns into a filing decision with no right answer, and that is the moment
maintenance stops. **The rule: the tree says what kind of thing it is, full stop.** The place is
a stock location. The purpose is a keyword (`keywords`, InvenTree's free-text search field; there
is no tag field). Two levels at most, and the food parent is `structural` so nothing can be filed
there directly.

The example food groups are the six from the German civil protection office's (BBK) ten-day
supply recommendation plus drinks and a catch-all, so "how many days are we covered" is a plain
group-by over the tree.

**A scan must not end in a form.** Three part fields remove almost all per-item typing, and the
plugin sets all three: `default_location`, `keywords` and, most importantly, `default_expiry`.
With that set, InvenTree fills in the best-before date of every new batch by itself, so the date
is typed once per product, not once per jar. `PartCategory` has no expiry field, so the per-group
default lives in `pantry.json` (`expiry_days`).

## Staples and varieties: two levels, and InvenTree already has them

Two brands of pasta used to know nothing of each other: buy once at one shop and once at
another, and the shopping list said "brand A: 2 missing" while three kilos of brand B sat in the
cellar. What was missing was a level. The **staple** ("pasta") carries the target stock and is
what you shop for. It has no barcode and no stock of its own. The **variety** ("brand B spaghetti
500 g") carries barcode, picture, dates, place and the real stock, and hangs under a staple.

This is InvenTree's own `is_template` / `variant_of`, verified before building on it: a template
reports `total_in_stock` as the sum over its variants, and `low_stock=true` compares against that
sum, so the template is short only when all brands together are, and the brands never are on
their own. The shopping list is therefore built from `low_stock`, the one source that gets
staples right *and* includes things that ran out entirely (those have no stock row at all).

⚠️ The `part_detail` on a stock row is a PartBrief and has **no `variant_of`**. The app loads the
catalogue separately for that reason.

## Counted or measured, and why the pack size lives on the product

Counting packets held for exactly one purchase: the first sack of rice showed a shortfall while
22.5 kg sat in the cellar, because two sacks are two packets. So a staple either **counts** pieces
(`units` empty, "toilet paper, 30 rolls") or **measures** a base unit (`units` = `kg`/`l`, "rice,
4 kg"). The unit belongs to the staple, not the brand. Passata counts jars even though every jar
says 500 g.

The **pack size is a parameter on the product** (`pack_parameter`), filled by the plugin from Open
Food Facts' `quantity` and correctable in the booking form. You book in packets; the app multiplies
and **stores the base amount**. That has to happen on write, not on display: InvenTree's own
`low_stock` compares against the stored number, and the shopping list comes from there.

⚠️ `minimum_stock` only accepts numbers that are exact binary fractions: 9.5 is fine, 9.6 is
refused (the serializer passes it through a float). Targets therefore snap to quarters, rounded
up. Stock quantities are not affected.

## When is "soon"?

A fixed number of days is wrong at both ends of the shelf: seven days before a three-year tin is
noise, seven days before milk that keeps ten is a warning at purchase. So the warning window
scales with shelf life and is clamped:

    lead = clamp(shelf_life × 0.2, 5 days, 90 days)

Shelf life is the **larger** of two numbers, and both are needed. The batch's own span (expiry
minus the day it was booked in) is honest when a batch really keeps longer than its kind:
batteries dated 2035 against a category default of two years. The product's `default_expiry` is
what the kind nominally keeps. Taking only the batch collapses during a stocktake: enter
everything on one day, and shelf life becomes time remaining for every row. That is how UHT milk
with 11 days to go stayed off the list (11 × 20 % = 2, floored to 5) while being the most urgent
thing in the house. The app and the watchdog compute this from the same three numbers in
`pantry.json`, so they cannot disagree.

## Safe orders of operations

InvenTree has no split endpoint, so splitting a batch into two dates is two calls. **Create the
new batch first, then take the amount off the old one.** If the second call fails you hold too
much, which is visible and one correction away. The other order loses stock with nothing to show
for it. Booking in follows the same thinking: it merges into an existing batch only if the date
matches too, because adding a fresh jar to an older batch would silently destroy the older date,
the one piece of information the app exists to keep.

## Languages

Every visible word comes from `app/i18n/<lang>.json`, looked up by `t("key")`. Plurals are
objects of forms picked by `Intl.PluralRules` (Russian needs three). Dates, numbers and sorting
use `Intl` with the language's locale. The date under a date field is spelled out with the month
as a word, because Chrome draws `<input type=date>` in the *browser's* language, not the page's,
and 09/10 against 10/09 is a food-safety question.

Matching a scanned product to a staple ("does 'Basmati rice' belong to 'rice'?") is plain word
overlap, category and habit. No embeddings, and that is deliberate: the category from Open Food
Facts has already narrowed the candidates to a handful, and every suggestion is one tap from being
corrected. Words come from `Intl.Segmenter`, because Chinese has no spaces between them.

A script checks the translations (`tools/check-app.py`): missing or extra keys, placeholders,
plural forms per locale. Adding a language is then a job an agent can finish and verify on its
own, which is the point.
