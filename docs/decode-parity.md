# Decode parity with sunpy

**Claim under test:** for the same FITS file, HelioFITS Studio's decoded physical values, observation
time and pixel-to-sky mapping equal what `sunpy.map.Map` gives, plus exactly the named corrections
below. It is not "equal to raw sunpy": Studio makes a few deliberate changes, and a difference that
is not one of them is a finding.

Status (2026-11-02): the Python side (`heliofits_core.parity`), the dump format and the Studio-side
dump tool are written and tested on **synthetic** files only. No real file has been compared yet:
the cloud session has no route to the archives, so the real run needs Gilly's Mac (see "Running it").

## First real run (2026-11-02)

Ten dumps, from one library file per instrument (AIA 131, SUVI 195, ASPIICS, LASCO C2 and C3, PUNCH CAM,
PUNCH PTM planes 0 to 2), LASCO also with the background subtracted. Studio at origin/master 7cfa47b57.
All numbers below were computed by this harness.

* Time: 0 ms on every file. NaN positions agree on every file. Orientation: no flip anywhere.
* WCS grid: largest deviation 8.5e-10 arcsec (assumed tolerance 1e-3).
* Data: AIA, LASCO (with and without background), ASPIICS: 0.0. SUVI: 3.8e-6 absolute, about one float32 ulp.
* **PUNCH failed**: Studio was exactly +0.5 x ZSCALE above astropy on every non-dithered tile-compressed
  pixel (nom-tam-fits 1.22.0 adds the half quantum that belongs to subtractive dithering). Fixed in Studio
  (`FITSImage.removeHalfQuantum`, `FitsQuantumCheck`); after the fix the largest PUNCH difference is 3.5e-18.
* Harness defects found and fixed: an `inf` pixel made the data tolerance infinite (ASPIICS passed vacuously),
  and the dump recorded Studio's shipped `normalize_aia = true`, which failed `settings_in_scope`.
* Seen, not fixed: `CCOR2_1A_20260902T211514_V00_NC.fits` (in Studio's test data) has 416,690 NaN pixels in
  astropy that Studio decodes as finite near-zero values.
* LASCO with background: the reference reuses Studio's own background array, so only the arithmetic is checked.

## Named corrections

Read from Studio's source on 2026-11-02 (`FITSImage.java`, `FitsMetaData.java`, `LascoPointing.java`).

| name | what Studio does that sunpy does not |
|---|---|
| `lasco_per_second` | LASCO only, **and only when the layer subtracts the NRL monthly background**: `(BZERO + raw*BSCALE - OFFSET) / EXPTIME - background`, blank and NaN pixels bad. With the switch off the frame is the archive's product, in DN, with only BZERO and BSCALE applied. |
| `time_prefers_date_avg` | Time is the first of `DATE-AVG`, `DATE_AVG`, `DATE_OBS`, `DATE-OBS` (LASCO: `DATE-OBS` with slashes made dashes, plus `TIME-OBS`). sunpy's `Map.date` reads `DATE-OBS` first. The reference uses `Map.date_average` when the file has `DATE-AVG`. |
| `plane_out_of_range_is_zero` | A plane index outside a cube reads plane 0 instead of an error. |
| `lasco_borrowed_pointing` | LASCO C2 frames of 2025-08-21 to 2025-08-31 with placeholder pointing take CROTA/CRPIX from a neighbouring frame. Not produced by the dump tool yet (it needs the probe of the whole request); such frames are noted, not compared. |

Not covered, on purpose: the display path (clip, stretch, the half-float texture, colour table, any
filter), the observer position (SDO is placed at Earth; `DSUN_OBS < 1` is replaced), and 3-D
coordinates. The comparison refuses a dump taken with `normalize_radius`, `normalize_aia` or a time
mode other than Observer, because those change the numbers by design.

Two Studio behaviours the dump records rather than corrects: an 8-bit image is shown as raw bytes with
BZERO/BSCALE unapplied (the dump says so in `notes`), and the half-float texture cannot be compared
with float32 at float32 precision, which is why the dump is taken before the texture.

## The dump (`hf-decode-dump/1`)

One JSON document plus one or two `.npy` files beside it. The same document is what the MCP's
`frame.get` should return (with `data.b64` in place of `data.file`; both are read).

```
format        "hf-decode-dump/1"
writer        {name, ...}                     who wrote it
source        {name, sha256, size}            the file, by hash
hdu, plane    which image, which plane of a cube (after the out-of-range rule)
instrument    AIA | LASCO | PUNCH | ASPIICS | SUVI | other
shape         [ny, nx]
orientation   "fits"                          row 0 is the LOWEST-y row, as in the file. Studio's textures are flipped; the dump is not.
bad_pixels    "nan"                           Studio's BAD_PIXEL sentinel (-2147483648) and blank pixels are written as NaN
unit          BUNIT, or "DN/s" after lasco_per_second
settings      {normalize_radius:false, normalize_aia:false, time_mode:"observer", filter:"none"}
corrections   [{name, applied, params}]       the named corrections that were applied
time          {keyword, iso}                  UTC, the observation time Studio reads
wcs           {grid_pixels, grid_arcsec}      zero-based pixel centres (x = column, y = row), up to a 9 x 9 grid with both ends included;
                                              helioprojective (Tx, Ty) in arcsec
data          {file | b64, encoding:"npy-float32-le"}   physical values, BZERO/BSCALE applied
background    same shape as data, only with lasco_per_second: the per-second array subtracted
notes         [text]
```

Values are float32. Infinities are kept; NaN marks bad pixels. Times are millisecond UTC.

## Tolerances and their basis

Every check in a report carries its tolerance and basis. Where a basis is an assumption it says so.

* **Data, whole numbers** (`np.isclose` with rtol = atol = 0): if the reference values are all integers
  below 2^24 and no arithmetic applies (no `lasco_per_second`), float32 holds them exactly, so any
  difference is a defect. This covers files stored as integers with an integer `BZERO` and `BSCALE = 1`.
* **Data, anything else**: `rtol = atol/max|ref| = 8 x 1.19e-7`. The float32 epsilon is a property of
  the type; the factor 8 is an **assumed** allowance for at most three float operations
  (`BZERO + raw*BSCALE`, the cast, and for LASCO the offset, the division and the subtraction). It is not
  measured, and not an instrument noise level. The first real run reports the largest difference seen, so
  the 8 can be replaced by an observed number.
* **Time**: 1 ms. Basis: Studio keeps observation time as whole milliseconds (`JHVTime.milli`).
* **WCS grid**: 1e-3 arcsec. **Assumed and provisional**: no measured basis exists yet. AIA's pixel is
  about 0.6 arcsec, so this is under 0.2 % of a pixel; the first real runs report the actual maximum and
  this value should be replaced by it (plus a stated margin).
* Bad pixels: the NaN positions must agree exactly.

If the data check fails, the report says when Studio's array equals the reference flipped or
transposed, because an orientation slip is the likeliest real defect.

## Running it

Cloud or any machine, on synthetic files: `pip install -e ".[parity,test]" && pytest tests/test_parity.py`.

Real files (needs the Mac, the library and Studio's `bin/`):

1. Pick fixtures: `python tools/parity_sample.py --library ~/HeliadLibrary --out parity-picks.json`
   (one file per instrument; LASCO one per detector; it lists any instrument the library lacks).
2. Dump each with Studio's decoder: copy `tools/studio/DecodeParityDump.java` to
   `src/org/helioviewer/jhv/view/uri/` in HelioFITS Studio (it must sit in `view.uri`, because
   `FITSImage.readData` is package-private), `ant compile`, then
   `java -Djava.awt.headless=true -cp "bin:resources:$(find lib -name '*.jar' | tr '\n' ':')" org.helioviewer.jhv.view.uri.DecodeParityDump OUT FILE.fits [--plane N] [--subtract-background]`.
   Use `--plane 0..2` on a PUNCH PTM, and `--subtract-background` on a LASCO frame (needs the network).
3. Compare: `HFPARITY_DIR=OUT HFPARITY_SOURCES=<library root> pytest tests/test_parity_real.py`, or one file:
   `python -m heliofits_core.parity compare OUT/NAME.p0.hfdump.json --source FILE.fits`.

`tools/studio/DecodeParityDump.java` was compile-checked against Studio's sources on 2026-11-02 with a JDK 21
(Studio builds with release 25, so unrelated files failed; none of the errors were in this file). It has
**not been run**.
