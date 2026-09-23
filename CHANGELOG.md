# Changelog

## 2.1.0 (2026-09-23)

- **Loading states on every button that waits for the server**: sign-in, barcode search,
  every form's confirm button (create, book in, use up, count, save batch, save article,
  split, both deletes), the staple suggestions, "new staple", and the photo upload (a spinner
  over the picture). The button is locked at once, a spinner replaces its label after 150 ms,
  and its Cancel is locked alongside. After a successful action the spinner stays until the
  page has been redrawn, so an impatient second tap cannot book the same thing twice.
- A form's own checks ("name is missing") no longer leave the confirm button disabled.
- The smoke test books and deletes against a slow fake API and fails without a spinner or
  with a double booking.

## 2.0.2 (2026-09-23)

- Loading: the start-up curtain now stays until the first screen has its data, instead of
  lifting onto empty "loading" cards. It shows the app's mark over the spinner and fades out;
  a first sign-in goes through the same curtain. A 15-second safety timer lifts it whatever
  happens. The smoke test reloads against a slow fake API and fails if the list is still
  empty when the curtain goes.

## 2.0.1 (2026-09-23)

- Header: the language code sat a few pixels above the name and the sign-out icon (the
  picker is a `<label>` and inherited the shared label margin). Everything in the header now
  shares one centre line.

## 2.0.0 (2026-09-23): first public release

- **Four languages**: German, English, Russian, Chinese. Every visible string moved into
  `app/i18n/<lang>.json`; plurals through `Intl.PluralRules`, dates and numbers through `Intl`
  in the reader's locale; units drawn in the reader's script (кг, 公斤). The app follows the
  phone's language, with a picker in the header. One home-screen manifest per language.
- **Product matching works without spaces**: words come from `Intl.Segmenter`, so Chinese
  product names are matched to staples; the old splitter only knew German letters.
- **One configuration file**, `pantry.json`, for the app, the plugin, the taxonomy script and
  the expiry watchdog. It replaces the values the private deployment baked in at install time.
  Starter taxonomies in four languages under `examples/`.
- **Expiry watchdog** takes its secrets from the environment, accepts a token or a password,
  writes its message in the household's language and prints to stdout when no channel is set.
- **Checks**: `tools/check-app.py` now verifies every language (keys, placeholders, plural
  forms, registration); new `tools/check-config.py` resolves every name in a `pantry.json`;
  new `tools/smoke-test.cjs` walks every screen in every language against a fake API.
- `tools/install.sh`, `deploy/` snippets for Caddy, Compose, nginx and systemd.

## 1.x (July–September 2026): private

Developed inside one household's infrastructure repository, deployed by Ansible. The history
of that period is summarised in `docs/design.md`.
