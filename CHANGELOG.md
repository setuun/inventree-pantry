# Changelog

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
