# Working on inventree-pantry (for AI agents)

This repository was written by an AI agent and is maintained the same way. These are the
rules that keep it working. Read them before you change anything.

## Layout

- `app/index.html`: the whole app: markup, CSS, ~2500 lines of inline JavaScript. No build
  step, no framework, no dependencies, no CDN, on purpose (docs/design.md). Keep it that way.
- `app/i18n/*.json`: every word on screen. `app/manifest.*.webmanifest`: home-screen names.
- `plugin/openfoodfacts_barcode.py`: InvenTree plugin (BarcodeMixin + SettingsMixin).
- `tools/`: taxonomy sync, expiry watchdog, installer, checks, smoke test.
- `examples/pantry.*.json`: starter configuration. `docs/configuration.md` describes every key.

## Rules

1. **No text in the code.** Anything a person reads goes through `t("key")` (script) or a
   `data-i18n` attribute (markup), and the key goes into **all** `app/i18n/*.json`, with
   English as the reference. A new key without translations fails the check; write the
   translations yourself and say they are machine-made.
2. **Run `python3 tools/check-app.py` after every edit of `app/`.** It exists because a text
   replacement with a wrong end anchor once deleted `showTab()` and the page was dead with
   perfectly valid syntax. If you change control flow or a screen, also run
   `node tools/smoke-test.cjs` (Playwright; see README) and look at the screenshots.
3. **Do not shadow `t`.** It is the translation function. A local `const t = …` inside a
   function that also calls `t("…")` breaks that function. Name loop variables `tpl`, `item`, ….
4. **Keep localStorage keys and the token name prefix** (`vorrat.token`, `vorrat.device`,
   `vorrat.lang`, device names `vorrat-xxxxxx`). Changing them signs every phone out and
   leaves orphaned tokens in InvenTree.
5. **InvenTree is the source of truth.** The app holds no state beyond the session. Every
   change is one documented API call, in an order that fails safe (see `panelSplit`: create
   the new batch first, then take the amount off the old one).
6. **Measured before assumed.** The comments cite what was verified against a live
   InvenTree and when. Keep them honest: if you did not verify something, do not write that
   you did. Several "obvious" shortcuts are wrong for reasons written right next to them;
   read the comment before simplifying.
7. **The same numbers in two places stay in one file.** The expiry rule (`expiry.*`) and the
   pack-size parameter name live in `pantry.json`, which the app, the plugin, the taxonomy
   script and the watchdog all read. Do not hard-code them anywhere.
8. **Bump `VERSION`** in `app/index.html` (and `PLUGIN_VERSION` in the plugin, if it changed)
   and add a `CHANGELOG.md` entry for anything user-visible.

## Commits

This repository is public. Commit as `setuun <6574023+setuun@users.noreply.github.com>`,
never with a personal address. The setting lives in `.git/config` and does not travel with a
clone, so on a fresh checkout run once:

    git config user.name "setuun"
    git config user.email "6574023+setuun@users.noreply.github.com"

## Checks

```sh
python3 tools/check-app.py
python3 tools/check-config.py examples/*.json
node tools/smoke-test.cjs [--shots DIR] [--viewport]
python3 -m py_compile plugin/*.py tools/*.py
```
