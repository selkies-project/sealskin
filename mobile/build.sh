#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# Version comes from the browser extension manifest so all release artifacts match.
VERSION=$(awk -F'"' '/"version":/ {print $4}' ../browser_extension/manifest.chrome.json)
if [ -z "$VERSION" ]; then
    echo "Error: Could not extract version from ../browser_extension/manifest.chrome.json"
    exit 1
fi
echo "Detected Version: $VERSION"
# Android versionCode must be an increasing integer: 0.2.8 -> 208
VERSION_CODE=$(echo "$VERSION" | awk -F. '{printf "%d", $1*10000 + $2*100 + $3}')

[ -d node_modules ] || npm install

# Capacitor refuses to add a platform until the web dir has an index.html.
mkdir -p www && cp index.html www/
[ -d android ] || npx cap add android

npm run build
npx cap sync android

# Stamp the app version into the generated gradle project.
sed -i "s/versionCode [0-9]*/versionCode ${VERSION_CODE}/; s/versionName \"[^\"]*\"/versionName \"${VERSION}\"/" android/app/build.gradle

APK_FILENAME="sealskin-v${VERSION}.apk"
rm -f "$APK_FILENAME"

if [ -n "${ANDROID_KEYSTORE_BASE64:-}" ]; then
    echo "Building release APK signed with provided keystore"
    KEYSTORE=$(mktemp)
    echo "$ANDROID_KEYSTORE_BASE64" | base64 -d > "$KEYSTORE"
    (cd android && ./gradlew assembleRelease)
    SDK_ROOT="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
    BUILD_TOOLS=$(ls -d "${SDK_ROOT}"/build-tools/* | sort -V | tail -n 1)
    "${BUILD_TOOLS}/apksigner" sign \
        --ks "$KEYSTORE" \
        --ks-pass "pass:${ANDROID_KEYSTORE_PASSWORD}" \
        --ks-key-alias "${ANDROID_KEY_ALIAS}" \
        --key-pass "pass:${ANDROID_KEY_PASSWORD}" \
        --out "$APK_FILENAME" \
        android/app/build/outputs/apk/release/app-release-unsigned.apk
    rm -f "$KEYSTORE"
else
    echo "ANDROID_KEYSTORE_BASE64 not set, building debug-signed APK"
    (cd android && ./gradlew assembleDebug)
    cp android/app/build/outputs/apk/debug/app-debug.apk "$APK_FILENAME"
fi

echo "Build Complete: $APK_FILENAME"
