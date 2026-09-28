#!/bin/sh
# Fetch the optional camera scanner for browsers without a native BarcodeDetector.
#
#     tools/fetch-scanner.sh
#
# Safari, and with it every browser on an iPhone, has no BarcodeDetector, so there the app can
# only take typed barcodes. This puts a polyfill into app/vendor/: the barcode-detector package
# (ZXing-C++ compiled to WebAssembly), about 1.1 MB, pinned by version AND sha512 below, which
# are the npm registry's own dist.integrity values. tools/install.sh copies app/vendor/ along
# with the app when it exists; the app loads it from its own origin, only where no native
# detector exists, so no phone ever talks to a CDN. Without it nothing changes.
#
# Needs curl, tar and sha512sum (or shasum). Safe to run again: it replaces app/vendor/.
set -eu

HERE=$(cd "$(dirname "$0")/.." && pwd)
VENDOR="$HERE/app/vendor"

# barcode-detector pins its zxing-wasm exactly (its package.json); move the two together.
BD_VERSION=3.2.2
BD_SHA512=ff840eaeb36b0919834815a288fe1a0bbd9d9e4b9750475c15189d1e06cf457529cbcd9770232f26cb08e9f12ce894f8d8b4bb0ef05564eef4a9ab096d82b2ac
ZX_VERSION=3.1.3
ZX_SHA512=de50bd0499387d1e592717068dbd215670c5396ecaa4b5c721e6e206655de050d141955e39bceda132b14543f1956c12831acc44e34ad2ee748410f56936042a

sha512() {
  if command -v sha512sum >/dev/null 2>&1; then sha512sum "$1" | cut -d' ' -f1
  else shasum -a 512 "$1" | cut -d' ' -f1; fi
}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

fetch() {  # name version sha512
  curl -fsSL -o "$TMP/$1.tgz" "https://registry.npmjs.org/$1/-/$1-$2.tgz"
  got=$(sha512 "$TMP/$1.tgz")
  if [ "$got" != "$3" ]; then
    echo "checksum mismatch for $1@$2 — refusing to install it" >&2
    exit 1
  fi
  mkdir "$TMP/$1"
  tar -xzf "$TMP/$1.tgz" -C "$TMP/$1"
}

fetch barcode-detector "$BD_VERSION" "$BD_SHA512"
fetch zxing-wasm "$ZX_VERSION" "$ZX_SHA512"

rm -rf "$VENDOR"
mkdir -p "$VENDOR"
# The IIFE build: a classic script that sets window.BarcodeDetector only where none exists.
cp "$TMP/barcode-detector/package/dist/iife/polyfill.js" "$VENDOR/barcode-detector.js"
cp "$TMP/zxing-wasm/package/dist/reader/zxing_reader.wasm" "$VENDOR/"
cp "$TMP/barcode-detector/package/LICENSE" "$VENDOR/LICENSE.barcode-detector"
cp "$TMP/zxing-wasm/package/LICENSE" "$VENDOR/LICENSE.zxing-wasm"
echo "scanner in $VENDOR (barcode-detector $BD_VERSION, zxing-wasm $ZX_VERSION)"
