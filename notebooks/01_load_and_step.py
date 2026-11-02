# %% [markdown]
# # 1. Load a dataset and step through time
#
# Needs a running HelioFITS Studio with its scripting API on (Settings > Scripting API), or the
# fake one: `python -m heliofits.testing` and `export HFSTUDIO_API=...` as it prints.
# The `sdo` times below are examples; change them.

# %%
from heliofits import studio

START, END = "2025-09-21T22:00", "2025-09-21T23:00"

# %% [markdown]
# Add a layer. `source="jp2"` is Helioviewer pictures; `"fits-library"` reads the HelioFITS Core
# cache (see notebook 3). Studio loads the frames and returns the layer's id.

# %%
layer = studio.layer_add(source="jp2", observatory="SDO", dataset="AIA 193", start=START, end=END, cadence_s=720)
print(layer)

# %% [markdown]
# Jump to a time, then walk the frames. `frame_get` writes the decoded values of one frame (the
# same document the parity test reads); `load_dump` turns it into numpy.

# %%
from heliofits.frames import load_dump

studio.seek_time(time="2025-09-21T22:35:47")
rows = []
for i in range(layer["frames"]):
    studio.seek_frame(frame=i)
    frame = load_dump(studio.frame_get(layer_id=layer["layer_id"], frame=i)["path"])
    rows.append((i, frame.time.isoformat(timespec="seconds"), float(frame.data.mean())))
for row in rows:
    print(row)
