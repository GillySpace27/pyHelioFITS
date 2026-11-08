# Reproducing an export from its record

**Claim:** anyone holding an export and its `.hfrec` sidecar can rebuild the picture with no other
input, and can say how close the rebuild is. Scope and decisions (Gilly, 2026-11-08) are in the
project's scope page; this file is the format and the Python side.

Status (2026-11-08): format 1.1 of the schema, the digests, the comparison and the preflight are
written and tested on synthetic PNGs. No real Studio export carries the new keys yet (Studio writes
them in its own PRs), and the `reproduce()` driver that launches an isolated Studio is not here yet.
The cross-GPU tolerance is **unmeasured**: nothing in this package assumes one.

## What "the same" means

Two exports of one scene never match byte for byte: the sidecar chunk Studio embeds carries a
fresh `record_id` (a random UUID) and a `created` time. So the identity of a picture is a digest of
its samples, not of its file.

`hfpix/1` is that digest: SHA-256 over

    "hfpix/1 <width> <height> <channels> <bit_depth>\n"

in ASCII, followed by every sample, row-major and channel-interleaved in the file's own channel
order (grey, grey+alpha, RGB or RGBA; a palette image is expanded to RGB or RGBA first), each as
a big-endian unsigned integer of `bit_depth` bits (8 or 16). The same picture gives the same digest
from a PNG, from Studio's frame buffer before encoding, or from any decoder.

The four verdicts of a comparison:

| verdict | meaning |
|---|---|
| `identical` | equal `hfpix/1` digests |
| `equivalent` | pixels differ by at most the tolerance given, and one was given |
| `different` | beyond the tolerance, no tolerance given, or sizes differ. If both environments are known and equal, the report says so: that is a bug or a hidden input, not the GPU |
| `cannot_replay` | the record (or the files at hand) do not allow a judgement |

## Format 1.1 keys

All additive: every 1.0 record is a valid 1.1 record, and a 1.0 reader ignores these.

| key | contents |
|---|---|
| `output.pixel_sha256` | `hfpix/1` of the export (a movie: of its first frame) |
| `output.content_sha256` | SHA-256 of the file with only IHDR, PLTE, tRNS, IDAT and IEND kept (PNG); null for movies |
| `output.frame_digests` | movies and series: `hfpix/1` of every output frame before encoding |
| `environment.os` | `name`, `version`, `arch` |
| `environment.jvm` | `version`, `vendor` |
| `environment.gpu` | `vendor`, `renderer`, `version`, `glsl`: the GL strings of the renderer that drew the export |
| `environment.display` | `dither`, `high_bit_depth_capture`, `pixel_ratio`, `msaa` |
| `environment.core` | `version`, `commit`, `python` of the HelioFITS Core that identified the sources |
| `environment.ffmpeg` | movies: `version`, `encoder`, `arguments` |
| `inputs_external[]` | `kind` (`star_catalog`, `model`, `body_ephemeris`, `overlay_feed`, `background`, `colour_table`, `font`, `other`), `name`, `version`, `sha256`, `source_url`, `pinned`, `caveat` |
| `reproducibility` | `exact_replay_possible`, `reasons[]`, `replay_scope` (`one` or `range`; absent means `one`), `frames_pending` |
| `companions.data_archive` | `file_name`, `sha256`, `size`, `scope` of the `hfstudio-session-data` zip written beside the export |

Never in `environment`: a hostname, a user name or a path. Records travel inside shared PNGs.

## Hidden inputs

Pinning the layers' source files by hash does not pin everything that shaped the picture. Read
from HelioFITS Studio master f8e2be4 on 2026-11-08; each row says what the record should carry.

| input | where it comes from | pin, hash or declare |
|---|---|---|
| Star field | `layers/stars/StarCatalog.java`, bundled `hipparcos-v9.csv` | pin: `star_catalog`, name `hipparcos`, version `v9` plus the resource's sha256; the build pins the file, the row makes it visible |
| 3D models | Model Manager files | hash each model file used (`model`, sha256) |
| Body positions | `identify/Bodies.java` and the ephemeris it reads | declare the ephemeris and its version (`body_ephemeris`); pinned when it is bundled, not when it is fetched |
| Overlay feeds (CME, comet, event lists) | live queries | declare (`overlay_feed`, `pinned: false`) with the query time; a feed answers differently later |
| LASCO monthly background | `FITSImage.java` background source, NRL's images or the user's own | hash the background array used (`background`, sha256) |
| Colour tables | built-in LUTs by name; user tables from `user-luts.txt` | built-in: the build pins them; user tables: hash (`colour_table`) |
| Dither | `imageCommon.frag` `dither()`: `fract(sin(dot(coord, nvec)) * 43758.5453)` of the pixel coordinate | a hash of the coordinate, so it repeats exactly at the same output size on the same GPU; off for high-bit-depth capture. Record `display.dither`; a replay at another size cannot match with it on |
| Fonts and labels | timestamp and credit overlays | declare the font (`font`); a different platform font moves pixels under the text |

## Using it

    hfcore record digest figure.png                  # the two digests
    hfcore record preflight figure.png.hfrec         # what the record says about replaying it
    hfcore record verify figure.png.hfrec replay/figure.png [--tolerance N] [--diff diff.png]

The same through the API as `record.digest` and `record.verify` (api.md), through the MCP server as
`cache_record_digest` and `cache_record_verify`, and in Python:

```python
from pyheliofits import reproduce

reproduce.digests("figure.png")["pixel_sha256"]
result = reproduce.compare("figure.png", "replay/figure.png", tolerance=2, diff_out="diff.png")
result.verdict, result.metrics, result.text()
result = reproduce.verify("figure.png.hfrec", "replay/figure.png")   # judged against the record
reproduce.preflight(record)                                           # reasons, scope, digests present
```

PNG decoding uses Pillow when installed (`pip install "pyheliofits[reproduce]"`) and a built-in
decoder otherwise, which handles non-interlaced 8- and 16-bit PNGs and is slow on large frames
with Average or Paeth filtering.

## Not here yet

* `reproduce()`: resolve sources (`record.resolve`), launch an isolated Studio, load the scene
  through its `sessionLoad` command, export at the recorded size, then `verify`. One timestamp by
  default, the whole range with `frames="range"`.
* EXR frames and movies: Studio digests frames before encoding; this reader opens PNGs only.
* The tolerance table: measured by replaying a golden set on two renderers (release gate
  `release/replay.sh`, companion of `release/parity.sh`).
