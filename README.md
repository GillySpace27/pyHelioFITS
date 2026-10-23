# AIA frames for the HFS icon

Full-disc SDO/AIA renders from Helioviewer (standard AIA colour tables) for the
X8.7 flare day, 2024-05-14, requested at 2024-05-14T16:51:00Z (about flare peak).
Channels 94, 131, 171, 193, 211, 304, 335, 1600, 1700 as `aia_<channel>.png`.

Settings: imageScale 1.9 arcsec/px, 1200x1200 px, centred on the Sun (x0=0, y0=0),
no watermark, no scale bar, no events. Layer string `[SDO,AIA,AIA,<channel>,1,100]`.

`times.json` holds the actual image time of each channel (from getClosestImage)
and the exact getClosestImage and takeScreenshot request URLs used.
These are Helioviewer renders (JPEG2000-derived, byte-scaled), not calibrated science data.
