#!/bin/bash

VERSION=$(awk -F'"' '/"version":/ {print $4}' manifest.chrome.json)
if [ -z "$VERSION" ]; then
    echo "Error: Could not extract version from manifest.chrome.json"
    exit 1
fi
echo "Detected Version: $VERSION"
FILES_TO_ZIP="icons *.js *.html *.css manifest.json"
CHROME_FILENAME="sealskin-chrome-v${VERSION}.zip"
echo "Building Chrome: $CHROME_FILENAME"
cp manifest.chrome.json manifest.json
rm -f "$CHROME_FILENAME"
zip -r "$CHROME_FILENAME" $FILES_TO_ZIP -x "*.DS_Store"
FIREFOX_FILENAME="sealskin-firefox-v${VERSION}.zip"
echo "Building Firefox: $FIREFOX_FILENAME"
cp manifest.firefox.json manifest.json
rm -f "$FIREFOX_FILENAME"
zip -r "$FIREFOX_FILENAME" $FILES_TO_ZIP -x "*.DS_Store"
rm manifest.json
echo "Build Complete!"
