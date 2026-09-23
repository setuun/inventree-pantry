# pantry.json

One file configures all four parts. Each part reads only the keys it needs and ignores the
rest, so one copy can go everywhere:

| copy | read by | installed by |
|---|---|---|
| `<data>/vorrat/config.json` | the app (fetched by the browser) | `tools/install.sh` |
| `<data>/plugins/pantry.json` | the plugin (on every scan), the taxonomy script | `tools/install.sh` |
| `/etc/inventree-pantry/pantry.json` | the expiry watchdog | by hand, INSTALL.md step 8 |

**Nothing secret belongs in it.** `vorrat/config.json` can be fetched without signing in,
like the page itself. Passwords and tokens for the watchdog go into its environment file.

Check a file with `python3 tools/check-config.py pantry.json`: it resolves every reference
below and fails on a name that points nowhere.

## Top level

| key | used by | default | meaning |
|---|---|---|---|
| `language` | app, watchdog | `"en"` | The household's language. The app uses it only when the phone's own language is not one it has; the watchdog writes its messages in it. |
| `pack_parameter` | app, plugin, taxonomy | `"Pack content"` | Name of the InvenTree parameter template that holds "how much is in one packet" per product. **All three must agree**, which is why it lives here. Renaming it later means renaming the template in InvenTree too. |
| `pack_parameter_description` | taxonomy | English text | Description of that template, shown in InvenTree's UI. Only used when the template is created. |

## `app`

| key | default | meaning |
|---|---|---|
| `book_location` | `""` | The shelf the booking form preselects, as a location path (`"Cellar/Emergency supply"`) or a plain location name. Empty: the product's default location, then the place its existing batches are in. |

## `expiry`: when is "soon"?

| key | default | meaning |
|---|---|---|
| `lead_fraction` | `0.2` | Share of a batch's shelf life used as its warning window. |
| `lead_min_days` | `5` | Never warn later than this many days before expiry. |
| `lead_max_days` | `90` | Never warn earlier than this. |
| `warn_days` | `7` | Only colours the date pill orange in the app. Keep it equal to InvenTree's `STOCK_STALE_DAYS`. |

`lead = clamp(shelf_life × lead_fraction, lead_min_days, lead_max_days)`, where shelf life is
the larger of the batch's own span (best-before date minus the day it was booked in) and the
product's default expiry. [design.md](design.md#when-is-soon) explains why it is the larger. The
app and the watchdog both compute this, from these same three numbers.

## `locations`

A list; each entry is an InvenTree stock location. Created if missing, `description` and
`parent` corrected if they drifted, never deleted.

| key | meaning |
|---|---|
| `name` | Required. Unique within the file. |
| `description` | Shown in InvenTree. |
| `parent` | Name of another location. **One level** of nesting (`Cellar/Emergency supply`). |

## `categories`

A list; each entry is an InvenTree part category. Same rules as locations.

| key | meaning |
|---|---|
| `name` | Required. Unique within the file. |
| `description` | Shown in InvenTree. |
| `parent` | Name of another category, one level only. |
| `structural` | `true`: parts cannot be filed here directly, only in a child. Use it on a parent like "Food", so everything does not pile up there. |
| `default_location` | Location name new parts in this category get. |
| `default_keywords` | Seeded into a new part's keywords (InvenTree's free-text tag field), e.g. `"emergency"`. |
| `expiry_days` | **Not an InvenTree field.** The plugin gives parts it creates here this `default_expiry`, and InvenTree then pre-fills the best-before date of every batch. `0` = no default. |

## `openfoodfacts`: the plugin

| key | default | meaning |
|---|---|---|
| `contact` | `""` | E-mail address in the User-Agent. Open Food Facts asks every client for one. |
| `fallback_location` | `""` | Location for a scanned product whose category has no `default_location`. Also the app's last-resort shelf. |
| `keywords` | `[]` | Keywords added to *every* part the plugin creates. |
| `label_keywords` | `{}` | Open Food Facts label → keyword, e.g. `"organic": "bio"`. Labels are OFF's tag names without the language prefix. |
| `category_map` | `[]` | Ordered list of `{"category": <name>, "tokens": [..]}`, see below. |
| `image_timeout` | `20` | Seconds for downloading the product photo (runs on InvenTree's worker, never delays a scan). |
| `image_max_kb` | `2048` | Photos larger than this are dropped, not truncated. |

**`category_map`** maps Open Food Facts' `categories_tags` onto your categories. The plugin
walks a product's tags from the most specific to the broadest (OFF lists them the other way
round), and for each tag tries the map entries in the order written; a token matches as a
substring of the tag without its language prefix (`en:canned-vegetables` → `canned-vegetables`
contains `vegetables`). The first hit wins. **Order is behaviour**: OFF files butter under both
`dairies` and `fats`, so whichever entry stands higher decides. Tokens are OFF's English tag
vocabulary in every language. Do not translate them.

The plugin's own settings in InvenTree (Admin → Plugins → Open Food Facts barcode lookup):
`AUTO_CREATE` (create parts, or only report the name), `CATEGORY` (the fallback category for
unmapped products, must be a leaf), `TIMEOUT` (seconds for the lookup), `FETCH_IMAGE`,
`LANGUAGE` (which localised product name to prefer).
