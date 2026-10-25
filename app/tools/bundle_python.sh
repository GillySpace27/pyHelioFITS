#!/bin/bash
# A relocatable Python with HelioFITS Core and its archive clients installed, for make_app.sh
# and for HelioFITS Studio's macOS app (its release/deploy_release.sh):
#
#   app/tools/bundle_python.sh                 # writes app/build/python
#   PYTHON_DIR=build/python SIGN_IDENTITY="Developer ID Application: " app/tools/make_app.sh
#
# The Python is a python-build-standalone "install_only" build for this Mac's
# architecture (latest release on GitHub, astral-sh/python-build-standalone),
# checked against the SHA-256 digest GitHub publishes for the asset. HelioFITS Core is
# installed from this checkout (not editable) with the archives extra.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${OUT:-build/python}"
PY_MINOR="${PY_MINOR:-3.12}"
case "$(uname -m)" in
    arm64) TRIPLE=aarch64-apple-darwin ;;
    x86_64) TRIPLE=x86_64-apple-darwin ;;
    *) echo "unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac
REPO_ROOT="$(cd .. && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Finding python-build-standalone $PY_MINOR for $TRIPLE"
curl -fsSL https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest -o "$WORK/release.json"
read -r NAME URL DIGEST < <(/usr/bin/python3 - "$WORK/release.json" "$PY_MINOR" "$TRIPLE" <<'PY'
import json, re, sys
release, minor, triple = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
pattern = re.compile(rf"^cpython-{re.escape(minor)}\.\d+\+\d+-{re.escape(triple)}-install_only\.tar\.gz$")
assets = [a for a in release["assets"] if pattern.match(a["name"])]
if len(assets) != 1:
    sys.exit(f"expected one matching asset in {release['tag_name']}, found {len(assets)}")
a = assets[0]
print(a["name"], a["browser_download_url"], a.get("digest") or "none")
PY
)
if [ -z "${URL:-}" ]; then
    echo "no python-build-standalone asset found" >&2
    exit 1
fi
echo "Downloading $NAME"
curl -fsSL "$URL" -o "$WORK/$NAME"
GOT="sha256:$(shasum -a 256 "$WORK/$NAME" | cut -d' ' -f1)"
if [ "$DIGEST" = "none" ]; then
    echo "GitHub published no digest for $NAME; refusing to use an unchecked download." >&2
    exit 1
fi
if [ "$GOT" != "$DIGEST" ]; then
    echo "checksum mismatch for $NAME: got $GOT, expected $DIGEST" >&2
    exit 1
fi
echo "Checksum matches ($DIGEST)"

tar -xzf "$WORK/$NAME" -C "$WORK"
rm -rf "$OUT"
mkdir -p "$(dirname "$OUT")"
mv "$WORK/python" "$OUT"
PY="$OUT/bin/python3"

echo "Installing heliofits-core[archives] from $REPO_ROOT"
# -s: without it pip counts packages in the build machine's ~/.local user site as
# already installed and leaves them out of the bundle (seen on the first real run).
"$PY" -s -m pip install --quiet --no-cache-dir --no-compile --disable-pip-version-check "$REPO_ROOT[archives]"
find "$OUT" -name __pycache__ -type d -prune -exec rm -rf {} +

"$PY" -B -s -c "import heliofits_core, sunpy, sunpy.net, drms, astropy, numpy; print('heliofits_core', heliofits_core.__version__, 'sunpy', sunpy.__version__, 'numpy', numpy.__version__)"
"$PY" -B -s -m heliofits_core --help > /dev/null
echo "Python ready in $OUT ($(du -sh "$OUT" | cut -f1))"
