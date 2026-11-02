# %% [markdown]
# # 2. Sweep a view parameter and export a still for each value
#
# The same moment at Box-Cox radial lambda 1 (linear), 0 (log) and -1 (reciprocal), one PNG each.
# This is the by-hand version of what the parameter-study job will do inside Studio.

# %%
from pathlib import Path

from heliofits import studio

OUT = Path("hfs-out/sweep")
OUT.mkdir(parents=True, exist_ok=True)

layer = studio.layer_add(source="jp2", dataset="SOHO/LASCO C2", start="2025-09-21T22:00",
                         end="2025-09-21T23:00", cadence_s=720)["layer_id"]
studio.seek_time(time="2025-09-21T22:35:47")

# %% [markdown]
# Filters take their own parameters. Pass them by name in `params` (the names come from the
# filter; look them up in Studio's filter dialog before sweeping one). Here the filter is set
# with its defaults and the sweep is over the view's lambda.

# %%
studio.filter_set(layer_id=layer, filter="rhef")
written = []
for lam in (1, 0, -1):
    studio.view_set(warp_lambda=lam)
    path = OUT / f"lambda_{lam:+d}.png"
    written.append(studio.export_still(path=str(path))["path"])
print(*written, sep="\n")
