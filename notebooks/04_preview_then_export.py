# %% [markdown]
# # 4. Preview, then export stills and a movie
#
# `preview_render` draws the scene as an export would look, to a temporary PNG, without
# saving an export. Look at it, change the view, preview again, and only then export.

# %%
from pathlib import Path

from pyheliofits import studio

OUT = Path("hfs-out/exports")
OUT.mkdir(parents=True, exist_ok=True)

layer = studio.layer_add(source="jp2", observatory="SDO", dataset="AIA 193", start="2025-09-21T22:00",
                         end="2025-09-21T23:00", cadence_s=720)
studio.seek_time(time="2025-09-21T22:35:47")

# %%
studio.view_set(projection="helioradialUnrolled")
preview = studio.preview_render(long_side=800)["path"]
print("look at", preview)  # in a notebook: from IPython.display import Image; Image(preview)

# %% [markdown]
# Happy with it? Export a still, then a movie of the loaded frames.

# %%
still = studio.export_still(path=str(OUT / "unrolled.png"), long_side=1600)
movie = studio.export_movie(path=str(OUT / "unrolled.mp4"), long_side=1600)
print(still["path"], movie["path"], movie["frames"], "frames")
