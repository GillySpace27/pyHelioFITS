# Heliad for Mac

The Heliad app: browse the library, search the archives, download, delete. It runs the
Python core (`heliad serve`) as a child process and talks JSON-RPC to it over pipes; the
core is the only thing that writes to the library.

    swift build                 # debug build
    swift test                  # HeliadKit tests; set HELIAD_CORE and HELIAD_TEST_LIBRARY
                                # to also run against a real core
    swift run Heliad            # run without a bundle
    tools/make_app.sh           # build/Heliad.app, signed ad hoc, with the heliad:// scheme

Which core it runs: `HELIAD_CORE`, then the `corePath` setting, then a Python bundled in
the app, then `~/Documents/NWRA/heliad/.venv/bin/heliad`. Which library: the core's
default (its config.json) unless the `libraryPath` setting names one. Both settings can
be given on the command line for one run: `Heliad -corePath PATH -libraryPath DIR`.

`Heliad -selfTest DIR -selfTestQuit YES` visits every screen against the real core,
saves a picture of each into DIR and writes DIR/report.json, for checking a build with
no one at the screen.

## Packaging

    tools/bundle_python.sh      # build/python: a standalone CPython 3.12 with heliad[archives]
    PYTHON_DIR=build/python SIGN_IDENTITY="Developer ID Application: ..." tools/make_app.sh

The first run on Gilly's Mac (2026-10-18) gave a 213 MB app (68 MB zipped) with 116
signatures: 115 Mach-O files in the Python, then the app, all with the hardened runtime and
a timestamp. Under the hardened runtime with no entitlements the bundled core searched VSO
and stored a real AIA file, and the self-test passed. Not yet notarized. To notarize, with a
notarytool keychain profile:

    ditto -c -k --keepParent build/Heliad.app build/Heliad.zip
    xcrun notarytool submit build/Heliad.zip --keychain-profile PROFILE --wait
    xcrun stapler staple build/Heliad.app

Requires macOS 14. Sources: `Sources/HeliadKit` (core client, models, the search form's
validation; no UI) and `Sources/Heliad` (SwiftUI).
