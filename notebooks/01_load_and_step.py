# %% [markdown]
# # 1. Load a dataset and step through time
#
# Needs a running HelioFITS Studio with its scripting API on (Settings > General > Local API), or the
# fake one: `python -m pyheliofits.testing` and `export HFSTUDIO_API=...` as it prints.
# The `sdo` times below are examples; change them.

# %%
from pyheliofits import studio

START, END = "2025-09-21T22:00", "2025-09-21T23:00"

# %% [markdown]
# Add a layer. `source="jp2"` is Helioviewer pictures; `"fits-library"` reads the HelioFITS Core
# cache (see notebook 3). Studio loads the frames and returns the layer's id.

# %%
layer = studio.layer_add(source="jp2", observatory="SDO", dataset="AIA 193", start=START, end=END, cadence_s=720)
print(layer)

# %% [markdown]
# Jump to a time, then walk the frames. `layer_list` says which frame is showing; `export_still`
# writes the frame on screen as a PNG. (`frame_get` is for FITS layers, `source="fits-library"`:
# it writes decoded values, and a jp2 picture has none.)

# %%
import tempfile
from pathlib import Path

out = Path(tempfile.mkdtemp())
studio.seek_time(time="2025-09-21T22:35:47")
rows = []
for i in range(layer["frames"]):
    studio.seek_frame(frame=i)
    png = studio.export_still(path=str(out / f"frame{i}.png"))["path"]
    shown = studio.layer_list()["layers"][0]["frame"]
    rows.append((i, shown, Path(png).stat().st_size))
for row in rows:
    print(row)
