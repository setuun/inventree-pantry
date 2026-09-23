# Adding or fixing a language

The app ships in German, English, Russian and Chinese. Any other language is a job an AI
agent can do in one go, because a script checks the result. The same steps work by hand.

> **To the agent:** do all five steps, then run `python3 tools/check-app.py` until it exits
> 0. If the person has Node, also run the smoke test (step 5) and look at the screenshots for
> text that overflows. Tell the person that the translation is machine-made and worth a read
> by a native speaker, especially the longer hints (`article.*Hint`, `requirement.newHint`).

## 1. The strings: `app/i18n/<code>.json`

Copy `app/i18n/en.json` to `app/i18n/<code>.json` (a two- or three-letter
[ISO 639](https://en.wikipedia.org/wiki/List_of_ISO_639_language_codes) code, lower case:
`fr`, `uk`, `pt`) and translate the **values**. Never change the keys.

- `{name}`, `{amount}`, `{n}` … are placeholders. Keep every one the English text has; you
  may move them. The check compares them per key.
- A value that is an **object** holds plural forms, chosen by the browser's
  `Intl.PluralRules` for `{n}`. Give exactly the forms your language uses. English and German
  use `one` and `other`, Russian and Ukrainian `one`, `few`, `many`, `other`, Chinese and
  Japanese only `other`, Arabic all six. The check asks Node which forms a locale needs and
  says what is missing or superfluous. A form may leave out `{n}` ("one product" rather
  than "1 product").
- The vocabulary matters more than any single sentence. Pick one word for each and use it
  everywhere:

  | concept | English | German | what it is |
  |---|---|---|---|
  | requirement | staple | Bedarf | what you shop for; holds the target, no stock of its own |
  | variety | variety | Sorte | a concrete product (brand) under a staple; holds the barcode |
  | batch | batch | Charge | one stock row: an amount, a place, one best-before date |
  | target | target stock | Soll-Bestand | how much you want to keep |
  | location | location | Lagerort | where it lies |

- `unit.kg.short` / `unit.l.short` are how kilograms and litres are *written* in your
  language (кг, 公斤). They are optional; without them the app writes "kg" and "l".
- `stock.note` is saved in InvenTree's stock history as the source of a booking, so keep it
  short.

## 2. The home-screen name: `app/manifest.<code>.webmanifest`

Copy `manifest.en.webmanifest`, translate `name`, `short_name` (what appears under the icon:
keep it short) and `description`, and set `lang`.

## 3. Register it: two lines in `app/index.html`

- In the `LANGUAGES` object: `xx: { name: "<name in the language itself>", locale: "<BCP 47>" },`
  in the same format as the lines around it. The locale decides how dates and numbers look:
  `fr-FR`, `pt-BR`, `uk-UA`.
- In the small script in `<head>`: add `"xx"` to `var have = [...]`.

## 4. Server side (optional)

- `tools/expiry-check.py`: add a block to `TEXT` with the same keys as `en`.
- `examples/pantry.<code>.json`: a translated starter taxonomy, if you want to offer one.
  Keep `openfoodfacts.category_map[].tokens` as they are. They match Open Food Facts' own
  English tags, not the language of the household. Run `tools/check-config.py` on it.
- `tools/check-config.py`: add the code to `LANGUAGES_WITH_TEXT`.

## 5. Check

```sh
python3 tools/check-app.py
node tools/smoke-test.cjs --shots /tmp/shots     # needs playwright, see README
```

The check finds missing or extra keys, broken placeholders, wrong plural forms and a
registration that is out of step. The smoke test opens every screen in every language and
fails if a raw key like `stock.heading` shows up anywhere; the screenshots show whether
longer words still fit.

## Fixing a translation

Edit the value in `app/i18n/<code>.json`, run the check. Nothing else is needed; the
phone picks it up on the next reload.
