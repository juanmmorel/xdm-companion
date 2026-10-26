#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
TAG="${1:-}"
OUTPUT_DIR="${2:-$PROJECT_DIR/dist/release}"
[[ "$TAG" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || {
  echo "Uso: scripts/package-release.sh v1.1.20 [directorio-de-salida]" >&2
  exit 2
}

CHROME_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$PROJECT_DIR/browser-bridge/manifest.chrome.json")"
FIREFOX_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$PROJECT_DIR/browser-bridge/manifest.firefox.json")"
NORMALIZED_TAG="${TAG#v}"
[[ "$CHROME_VERSION" == "$FIREFOX_VERSION" ]] || { echo "Las versiones de Chrome y Firefox no coinciden." >&2; exit 1; }
[[ "$CHROME_VERSION" == "$NORMALIZED_TAG" ]] || {
  echo "El tag $TAG debe coincidir con la versión $CHROME_VERSION de los manifiestos." >&2
  exit 1
}

mkdir -p "$OUTPUT_DIR"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/xdm-companion-release.XXXXXXXX")"
trap 'rm -rf -- "$STAGE"' EXIT

mkdir -p "$STAGE/core" "$STAGE/installer" "$STAGE/browser-bridge"
cp -a "$PROJECT_DIR/core/." "$STAGE/core/"
cp -a "$PROJECT_DIR/browser-bridge/." "$STAGE/browser-bridge/"
cp "$PROJECT_DIR/installer/install.py" "$PROJECT_DIR/installer/uninstall.py" \
  "$PROJECT_DIR/installer/browser_bridge.py" "$STAGE/installer/"
cp "$PROJECT_DIR/requirements.txt" "$PROJECT_DIR/README.md" "$PROJECT_DIR/LICENSE" "$STAGE/"
find "$STAGE" -type d -name __pycache__ -prune -exec rm -rf -- {} +
find "$STAGE" -type f \( -name '*.pyc' -o -name '*.bck' \) -delete

ASSET="$OUTPUT_DIR/$TAG/xdm-companion-linux.tar.gz"
mkdir -p "$(dirname "$ASSET")"
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-0}"
tar --sort=name --owner=0 --group=0 --numeric-owner --mtime="@$SOURCE_DATE_EPOCH" \
  --directory "$STAGE" --create --file - . | gzip --no-name > "$ASSET"
(cd "$(dirname "$ASSET")" && sha256sum "$(basename "$ASSET")" > "$(basename "$ASSET").sha256")

echo "$ASSET"
echo "$ASSET.sha256"
