# %% [markdown]
# # 5. Beyond the GUI: a height-time curve for each event in a list
#
# Studio has no "run this over a list of events and measure something" button. Here a script
# loads LASCO C2 around each event, exports a still, pulls every frame into numpy, takes running
# differences, and records how far out the bright front reaches.
#
# This is a demonstration of the workflow, not a validated CME tracker: the front is the
# outermost pixel above a plain threshold in the running difference, in the plane of the sky, with
# no occulter masking or position-angle selection. Edit `EVENTS` or put an `events.csv`
# (columns `name,start,end`) beside the notebook.

# %%
import csv
from pathlib import Path

import numpy as np

from pyheliofits import studio
from pyheliofits.frames import load_dump

OUT = Path("hfs-out/cme")
OUT.mkdir(parents=True, exist_ok=True)
RSUN_ARCSEC = 960.0  # approximate solar radius at 1 AU; use your own value for real work
SIGMA = 5.0          # front = pixels this many standard deviations above the running difference's mean

EVENTS = [{"name": "example", "start": "2025-09-21T22:00", "end": "2025-09-21T23:00"}]
if Path("events.csv").exists():
    EVENTS = list(csv.DictReader(open("events.csv", newline="")))

# %%
def sun_centre_px(frame):
    """Pixel where the dump's own grid says Tx = Ty = 0 (a straight-line fit per axis)."""
    px, sky = frame.grid_pixels, frame.grid_arcsec
    ax = np.polyfit(sky[:, 0], px[:, 0], 1)
    ay = np.polyfit(sky[:, 1], px[:, 1], 1)
    return ax[1], ay[1]


def front_heights(frames):
    cx, cy = sun_centre_px(frames[0])
    scale = frames[0].arcsec_per_pixel()
    yy, xx = np.indices(frames[0].data.shape)
    radius = np.hypot(xx - cx, yy - cy) * scale / RSUN_ARCSEC
    heights = []
    for prev, cur in zip(frames, frames[1:]):
        diff = cur.data - prev.data
        hot = diff > np.nanmean(diff) + SIGMA * np.nanstd(diff)
        heights.append(float(radius[hot].max()) if hot.any() else float("nan"))
    return heights

# %%
summary = []
for ev in EVENTS:
    layer = studio.layer_add(source="jp2", observatory="SOHO", dataset="LASCO C2", start=ev["start"], end=ev["end"])
    lid = layer["layer_id"]
    studio.seek_frame(frame=layer["frames"] // 2)
    studio.export_still(path=str(OUT / f"{ev['name']}.png"))
    frames = [load_dump(studio.frame_get(layer_id=lid, frame=i)["path"]) for i in range(layer["frames"])]
    heights = front_heights(frames)
    with open(OUT / f"{ev['name']}_height_time.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_utc", "front_rsun"])
        w.writerows((fr.time.isoformat(timespec="seconds"), h) for fr, h in zip(frames[1:], heights))
    studio.layer_remove(layer_id=lid)
    summary.append((ev["name"], layer["frames"], heights[0], heights[-1]))
for row in summary:
    print(row)

# %% [markdown]
# Open the CSVs in your plotting tool of choice, or plot with matplotlib if it is installed.
