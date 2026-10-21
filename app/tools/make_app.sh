#!/bin/bash
# Build Heliad.app from this Swift package: a release build of the Heliad binary,
# wrapped with an Info.plist (bundle id, the heliad:// URL scheme) and signed.
#
#   app/tools/make_app.sh                      # ad hoc signature, for this Mac
#   SIGN_IDENTITY="Developer ID Application: Name (TEAMID)" app/tools/make_app.sh
#   PYTHON_DIR=/path/to/python app/tools/make_app.sh   # bundle a Python with heliad installed
#
# The app is written to app/build/Heliad.app.
set -euo pipefail
cd "$(dirname "$0")/.."

BUNDLE_ID="io.github.gillyspace27.heliad"
IDENTITY="${SIGN_IDENTITY:--}"
APP="build/Heliad.app"
VERSION="$(git describe --tags --always 2>/dev/null || echo dev)"

# Build exactly the dependencies Package.resolved names; a stale resolved file fails here.
swift build -c release --disable-automatic-resolution
BIN="$(swift build -c release --disable-automatic-resolution --show-bin-path)/Heliad"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Heliad"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
    <key>CFBundleName</key><string>Heliad</string>
    <key>CFBundleDisplayName</key><string>Heliad</string>
    <key>CFBundleExecutable</key><string>Heliad</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>0.1.0</string>
    <key>CFBundleVersion</key><string>${VERSION}</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>CFBundleURLTypes</key>
    <array>
        <dict>
            <key>CFBundleURLName</key><string>${BUNDLE_ID}</string>
            <key>CFBundleURLSchemes</key><array><string>heliad</string></array>
        </dict>
    </array>
</dict>
</plist>
PLIST

if [ -n "${PYTHON_DIR:-}" ]; then
    # A relocatable Python (python-build-standalone) with heliad and its dependencies
    # installed; the app runs it as Contents/Resources/python/bin/python3 -m heliad.
    cp -R "$PYTHON_DIR" "$APP/Contents/Resources/python"
    find "$APP/Contents/Resources/python" -name __pycache__ -type d -prune -exec rm -rf {} +
fi

# Retries with growing waits: a Developer ID signature asks Apple's timestamp server for
# every file, and on Gilly's Mac (2026-10-18) it failed on and off for 10 s or more ("A
# timestamp was expected but was not found"), which one retry after 3 s did not cover.
sign() {
    local wait
    for wait in 5 10 20 40; do
        codesign --force --options runtime --timestamp --sign "$IDENTITY" "$1" && return 0
        echo "codesign failed on $1; trying again in $wait s" >&2
        sleep "$wait"
    done
    codesign --force --options runtime --timestamp --sign "$IDENTITY" "$1"
}

if [ "$IDENTITY" = "-" ]; then
    codesign --force --deep --sign - "$APP"
else
    # Every Mach-O inside the bundled Python is signed first, then the app, with the
    # hardened runtime that notarization requires.
    if [ -d "$APP/Contents/Resources/python" ]; then
        find "$APP/Contents/Resources/python" -type f \( -name '*.so' -o -name '*.dylib' -o -perm -u+x \) -print0 |
            while IFS= read -r -d '' f; do
                if file -b "$f" | grep -q Mach-O; then
                    sign "$f"
                fi
            done
    fi
    sign "$APP"
fi
codesign --verify --strict --verbose=2 "$APP"
echo "Built $APP ($VERSION)"
