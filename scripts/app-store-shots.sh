#!/bin/bash
# Retake the Mac App Store screenshots in one run, in the order STORE.md lists them.
#
#   scripts/app-store-shots.sh              # the four shots, into screenshots/<VER>/
#   scripts/app-store-shots.sh --dry-run    # print the steps, touch nothing
#   VER=1.4.0-retake scripts/app-store-shots.sh   # a fresh folder (nothing is wiped)
#
# It drives the INSTALLED HelioFITS (open -a HelioFITS), never a Debug build, and
# captures with make-screenshots.sh, so the captions and the 2560x1600 canvas are
# the same as a hand run. Sample files are the bundled, synthetic-safe cutouts in
# HelioFITS/Samples/. Nothing is uploaded: dragging screenshots/<VER>/ into App
# Store Connect stays a person's step.
#
# One-time setup: System Settings > Privacy & Security > Accessibility, allow the
# Terminal you run this from (the script clicks the toolbar buttons) and Screen
# Recording (screencapture).
#
# First run on a Mac (HF-17): the toolbar click needed a list loop, the Finder shot
# needed a single-window grab, and the viewer window has no Header chip (the header
# pane is always beneath the image; only Stretch is toggled, and only if needed).
# Still look at each PNG before uploading: shot 1 came from qlmanage and its title
# bar read "[DEBUG] <file>"; if that shows, take shot 1 by hand (Space in Finder).
set -euo pipefail
cd "$(dirname "$0")/.."

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1
AIA="HelioFITS/Samples/Sample-AIA.fits"
PUNCH="HelioFITS/Samples/Sample-PUNCH.fits"
[ -f "$AIA" ] && [ -f "$PUNCH" ] || { echo "samples missing under HelioFITS/Samples/"; exit 1; }

# make-screenshots.sh needs a python3 that can import Quartz (pyobjc) and PIL.
# The system python3 has no Quartz, and a shell started from an app has a short
# PATH, so find a python3 that can and put it first.
if ! python3 -c 'import Quartz, PIL' 2>/dev/null; then
    for cand in "$HOME/miniconda3/bin/python3" "$HOME/miniconda3a/bin/python3" \
                /opt/homebrew/bin/python3 /usr/local/bin/python3; do
        if [ -x "$cand" ] && "$cand" -c 'import Quartz, PIL' 2>/dev/null; then
            PYSHIM="$(mktemp -d)"; ln -s "$cand" "$PYSHIM/python3"
            export PATH="$PYSHIM:$PATH"; echo "using $cand for screenshots"; break
        fi
    done
fi
python3 -c 'import Quartz, PIL' 2>/dev/null \
    || { echo "need a python3 with pyobjc-framework-Quartz and Pillow"; exit 1; }

run() { echo "+ $*"; [ "$DRY" = 1 ] || "$@"; }

# Open or close the Stretch panel of the front HelioFITS window, whatever state the
# app remembered. The panel is open when its sliders exist in the accessibility tree.
# (Loop over a list: "first button of (entire contents of ...) whose ..." is not valid.)
STRETCH_AS='
on run argv
    set want to item 1 of argv
    tell application "HelioFITS" to activate
    delay 1
    tell application "System Events"
        tell process "HelioFITS"
            set els to entire contents of window 1
            set isOpen to false
            repeat with e in els
                try
                    if role of e is "AXSlider" then set isOpen to true
                end try
            end repeat
            if (want is "open" and isOpen) or (want is "close" and not isOpen) then return "already " & want
            repeat with e in els
                try
                    if role of e is "AXButton" and description of e is "Adjust brightness stretch" then
                        click e
                        return "clicked"
                    end if
                end try
            end repeat
            error "Stretch button not found"
        end tell
    end tell
end run'
stretch() { run osascript -e "$STRETCH_AS" "$1"; }
quit_app() { run osascript -e "tell application \"$1\" to quit" || true; run sleep 2; }

echo "==> 1. Quick Look preview of an AIA frame"
echo "+ qlmanage -p $AIA &"
[ "$DRY" = 1 ] || qlmanage -p "$AIA" >/dev/null 2>&1 &
run sleep 4
OWNER=qlmanage CAPTIONS="Press Space in Finder: the Sun, in the right colors" run ./make-screenshots.sh
run pkill -x qlmanage || true

echo "==> 2. A Finder folder of FITS files, icon view"
GALLERY="$(mktemp -d)/Solar data"
run mkdir -p "$GALLERY"
run cp "$AIA" "$PUNCH" "$GALLERY/"
run osascript -e 'tell application "Finder"' -e "open POSIX file \"$GALLERY\"" \
    -e 'set current view of front Finder window to icon view' -e 'activate' -e 'end tell'
run sleep 4
# make-screenshots.sh with OWNER=Finder grabs EVERY big Finder window on screen, so
# grab just the front one (the folder opened above) and compose that.
FWID=0
[ "$DRY" = 1 ] || FWID="$(osascript -e 'tell application "Finder" to get id of front window')"
FSHOT="$(mktemp -d)/finder-front.png"
run screencapture -x -o -l "$FWID" "$FSHOT"
run ./make-screenshots.sh --compose "$FSHOT" "Your data folder, in the right colors"
run osascript -e 'tell application "Finder" to close front Finder window'

echo "==> 3. Viewer with the header beneath and the stretch panel open"
quit_app HelioFITS
run open -a HelioFITS "$AIA"
run sleep 9
# The viewer window always shows the header pane beneath the image (the "Header"
# toolbar chip exists only in Quick Look), so only the stretch panel needs opening.
stretch open
run sleep 2
CAPTIONS="Image, header and stretch in one window" run ./make-screenshots.sh

echo "==> 4. The header pane showing the metadata rows"
stretch close   # close the stretch panel again
run sleep 2
CAPTIONS="Every header card, one click away" run ./make-screenshots.sh

quit_app HelioFITS
echo "==> done. Check each PNG, then drag the folder into App Store Connect (Media Manager)."
