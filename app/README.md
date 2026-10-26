# HelioFITS Cache Manager

The Mac app for HelioFITS Core (both were called Heliad until 2026-10-25): browse the library, search the archives, download, delete. It runs the
Python core (`hfcore serve`) as a child process and talks JSON-RPC to it over pipes; the
core is the only thing that writes to the library.

What it shows:

- **Timeline**: every dataset on one UTC time axis (`library.coverage`), darker where more
  frames are kept and blank in the gaps. Drag to select a range, then zoom to it, open a
  dataset's files in it, search the archives for it, or make it an event.
- **A dataset's files**: a grid of previews or a sortable table, filtered by time.
- **Frame viewer** (View > Show Frame Viewer, hidden by default): one frame at a time at
  1024 px, stepped or played in time order, with a mark per frame so cadence gaps show, the
  header checks, and Show in Finder, Copy Path and Delete. It is for checking what the library
  keeps; it composes, overlays and exports nothing, which is HelioFITS Studio's work.
- **Search**: the archives' tables, and where the archive reports record times, its coverage
  over the range drawn above what the library already holds, with the columns that have
  archive records and nothing kept marked.
- **Events**: a named range of chosen datasets (`view.create`), with its folder of links
  beside the library (`views/<name>`, `view.materialize`). The index is the event of record.
- **Downloads**: jobs, their progress, cancel and retry.
- **Record inspector** (File > Open Processing Record, or drop an exported file on the
  window): what made a Studio export, from its processing record (`record.read`): the summary,
  the layers top to bottom with their steps in order, each source's place in the library
  (`record.resolve`), Fetch Missing Sources, the citations to copy, and Open in Studio, which
  launches Studio with `-dataset ID -start ISO -end ISO` (options being added to Studio; macOS
  passes them only when Studio is not already running).

    swift build                 # debug build
    swift test                  # CacheManagerKit tests; set HFCORE_CORE and HFCORE_TEST_LIBRARY
                                # to also run against a real core
    swift run CacheManager      # run without a bundle
    tools/make_app.sh           # build/HelioFITS Cache Manager.app, signed ad hoc, with the hfcore:// and heliad:// schemes

Which core it runs: `HFCORE_CORE` (or `HELIAD_CORE`, its old name), then the `corePath` setting, then a Python bundled in
the app, then `~/Documents/NWRA/heliad/.venv/bin/heliad`. Which library: the core's
default (its config.json) unless the `libraryPath` setting names one. Both settings can
be given on the command line for one run: `CacheManager -corePath PATH -libraryPath DIR`.

`CacheManager -selfTest DIR -selfTestQuit YES` visits every screen against the real core,
saves a picture of each into DIR and writes DIR/report.json, for checking a build with
no one at the screen. Add `-showFrameViewer YES` to include the frame viewer; given on the
command line it holds for that run only and is not saved.

## Packaging

    tools/bundle_python.sh      # build/python: a standalone CPython 3.12 with heliofits-core[archives]
    PYTHON_DIR=build/python SIGN_IDENTITY="Developer ID Application: ..." tools/make_app.sh

The first run on Gilly's Mac (2026-10-18) gave a 213 MB app (68 MB zipped) with 116
signatures: 115 Mach-O files in the Python, then the app, all with the hardened runtime and
a timestamp. Under the hardened runtime with no entitlements the bundled core searched VSO
and stored a real AIA file, and the self-test passed. Apple notarized that build (commit
3197f46) on 2026-10-19 in about two minutes. To notarize, with a notarytool keychain profile:

    ditto -c -k --keepParent "build/HelioFITS Cache Manager.app" build/CacheManager.zip
    xcrun notarytool submit build/CacheManager.zip --keychain-profile PROFILE
    xcrun notarytool info ID --keychain-profile PROFILE     # until Accepted
    xcrun stapler staple "build/HelioFITS Cache Manager.app"

Submit without `--wait`: HelioFITS Studio's release notes (2026-09-23) record it crashing
mid-upload. Only "Successfully uploaded file" means the upload finished. Zip the app again
after stapling if the zip is what gets handed out.

Requires macOS 14. Sources: `Sources/CacheManagerKit` (core client, models, the search form's
validation; no UI) and `Sources/CacheManager` (SwiftUI).
