#!/bin/sh
# Copy inventree-pantry into an InvenTree data directory.
#
#     tools/install.sh <inventree-data-dir> <pantry.json>
#
# <inventree-data-dir> is the directory the upstream docker-compose.yml calls
# INVENTREE_EXT_VOLUME — the one that already holds static/, media/ and plugins/. This script:
#
#   1. runs both checks (app and config) and stops if either fails
#   2. puts the app into <dir>/vorrat/, with your pantry.json as vorrat/config.json (and
#      app/vendor/, the optional iPhone scanner from tools/fetch-scanner.sh, if present)
#   3. puts the Open Food Facts plugin and the same pantry.json into <dir>/plugins/
#   4. gives everything the owner of <dir>, so the container (uid 1000 upstream) can read it
#
# It changes nothing else. The Caddy route, the proxy volume, the restart and the plugin
# activation are the steps printed at the end; INSTALL.md walks through them. Safe to run
# again for an update: files are replaced, nothing is deleted.
set -eu

if [ $# -ne 2 ]; then
  sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
fi

DATA=$1
CONFIG=$2
HERE=$(cd "$(dirname "$0")/.." && pwd)

[ -d "$DATA" ] || { echo "not a directory: $DATA" >&2; exit 1; }
[ -f "$CONFIG" ] || { echo "no such file: $CONFIG" >&2; exit 1; }

python3 "$HERE/tools/check-app.py" "$HERE/app"
python3 "$HERE/tools/check-config.py" "$CONFIG"

OWNER=$(stat -c '%u:%g' "$DATA")

mkdir -p "$DATA/vorrat/i18n" "$DATA/plugins"
cp "$HERE"/app/index.html "$HERE"/app/icon.svg "$HERE"/app/manifest.*.webmanifest "$DATA/vorrat/"
cp "$HERE"/app/i18n/*.json "$DATA/vorrat/i18n/"
# Optional: the camera scanner for iPhones, from tools/fetch-scanner.sh.
if [ -d "$HERE/app/vendor" ]; then
  mkdir -p "$DATA/vorrat/vendor"
  cp "$HERE"/app/vendor/* "$DATA/vorrat/vendor/"
fi
cp "$CONFIG" "$DATA/vorrat/config.json"
cp "$HERE/plugin/openfoodfacts_barcode.py" "$DATA/plugins/"
cp "$CONFIG" "$DATA/plugins/pantry.json"
chown -R "$OWNER" "$DATA/vorrat" "$DATA/plugins/openfoodfacts_barcode.py" "$DATA/plugins/pantry.json" 2>/dev/null \
  || echo "note: could not chown to $OWNER — run as root, or check the container can read the files" >&2

cat <<EOF
Copied. Still to do, once (INSTALL.md has each step in full):
  - Caddyfile: add deploy/Caddyfile.snippet inside InvenTree's site block
  - Compose:   add the vorrat volume to inventree-proxy (deploy/docker-compose.override.yml)
  - restart:   docker compose up -d && docker compose restart inventree-server inventree-worker
  - activate:  the "Open Food Facts barcode lookup" plugin in InvenTree's admin settings
  - taxonomy:  docker exec -i inventree-server sh -c 'cd /home/inventree/src/backend/InvenTree && python3 manage.py shell' < tools/sync-taxonomy.py
  - settings:  STOCK_ENABLE_EXPIRY = true (INSTALL.md, step 6)
Then open <your InvenTree>/vorrat/ on a phone.
EOF
