#!/bin/bash
# Build the browser extension zips from the shared client build.
#
# Output: browser_extension/sealskin-chrome-v<VERSION>.zip and
#         browser_extension/sealskin-firefox-v<VERSION>.zip
set -euo pipefail
cd "$(dirname "$0")"

REPO_DIR="$(cd .. && pwd)"
VERSION="$(tr -d '[:space:]' < "${REPO_DIR}/VERSION")"
if [ -z "$VERSION" ]; then
    echo "Error: VERSION file is empty"
    exit 1
fi
echo "Detected Version: $VERSION"

DIST="${REPO_DIR}/client/dist/extension"
(
    cd "${REPO_DIR}/client"
    [ -d node_modules ] || npm install --no-audit --no-fund
    SEALSKIN_BUILD_STRICT=1 npm run build --silent -- --target extension
)
if [ ! -f "${DIST}/background.js" ] || [ ! -f "${DIST}/manifest.chrome.json" ]; then
    echo "Error: client build did not produce ${DIST}"
    exit 1
fi

for BROWSER in chrome firefox; do
    ZIP="${PWD}/sealskin-${BROWSER}-v${VERSION}.zip"
    echo "Building ${BROWSER}: $(basename "$ZIP")"
    rm -f "$ZIP"
    (
        cd "$DIST"
        cp "manifest.${BROWSER}.json" manifest.json
        zip -qr "$ZIP" . -x "manifest.chrome.json" -x "manifest.firefox.json" -x "*.DS_Store"
        rm -f manifest.json
    )
done
echo "Build Complete!"
