# %% [markdown]
# # 6. Reproduce a figure from its sidecar
#
# Every export HelioFITS Studio writes can carry a `.hfrec` record beside it (`figure.png` and
# `figure.png.hfrec`). The record holds the whole scene, so someone with only those two files can
# rebuild the picture and say how close the rebuild is. This notebook shows the parts:
#
# 1. a picture's identity is a digest of its pixels, not of its file (two exports never match byte for byte);
# 2. what a record says about replaying itself, before anything is loaded;
# 3. the replay: an isolated Studio loads the record, exports again, and the result is judged.
#
# Parts 1 and 2 run against any Studio, or the fake one (`python -m pyheliofits.testing`).
# Part 3 needs a real Studio with the `sessionLoad` command and an export with its `.hfrec`;
# set `HFREC` to that sidecar's path to run it. Install the helper that reads PNGs with
# `pip install "pyheliofits[reproduce]"` (a built-in reader covers the rest).

# %% [markdown]
# ## 1. The identity of a picture
#
# Export the same scene twice. The files differ (each embeds a fresh record id and time), but the
# `hfpix/1` pixel digest does not.

# %%
import tempfile
from pathlib import Path

from pyheliofits import reproduce, studio

out = Path(tempfile.mkdtemp())
studio.layer_add(source="jp2", observatory="SDO", dataset="AIA 193", start="2025-09-21T22:00",
                 end="2025-09-21T23:00", cadence_s=720)
studio.seek_time(time="2025-09-21T22:35:47")
first = studio.export_still(path=str(out / "first.png"), long_side=64)["path"]
second = studio.export_still(path=str(out / "second.png"), long_side=64)["path"]
print(reproduce.digests(first)["pixel_sha256"])

# %% [markdown]
# `compare` gives one of four verdicts: `identical` (equal digests), `equivalent` (differs by no
# more than a tolerance **you** pass; none is assumed), `different`, or `cannot_check` (different
# sizes, or nothing to compare). It also reports how large the difference is.

# %%
result = reproduce.compare(first, second)
print(result.verdict)
print(result.text())

# %% [markdown]
# ## 2. What a record says about replaying itself
#
# `preflight` reads only the record. `exact` is false whenever the record names a reason the
# pixels might not come back the same (a layer that fetches live data, an unpinned input, a build
# with uncommitted changes). `can_replay` is false only when there is nothing to rebuild from.

# %%
import os

from pyheliofits.core import record

HFREC = os.environ.get("HFREC")  # e.g. "/path/to/figure.png.hfrec"
if HFREC:
    rec = record.read(HFREC)["record"]
    pre = reproduce.preflight(rec)
    print("can replay:", pre["can_replay"], "| exact:", pre["exact"], "| frames:", pre["frames"])
    for reason in pre["reasons"]:
        print(" -", reason)
else:
    print("Set HFREC to an export's .hfrec to run parts 2 and 3.")

# %% [markdown]
# ## 3. Replay
#
# `launch_studio` starts a **separate** Studio with its own scratch home, so your own Studio, its
# settings and its library are never touched. You supply the command that starts Studio on your
# machine, with `{home}` where the scratch home goes. `reproduce` then loads the record
# (`sessionLoad`), exports a still at the recorded size and judges it against the record's digest.
#
# A movie replays its **first frame** unless you pass `frames="range"`, which loads the whole
# series (and fetches all its data) and judges every frame against the per-frame digests in the
# record. Use `range` to judge a movie: on one test movie `frames="one"` came back `different`
# where `range` came back `identical`, and the cause is not yet known.

# %%
LAUNCH = ["java", "-Duser.home={home}", "-jar", "HFStudio.jar"]  # change to how you start Studio

if HFREC and pre["can_replay"]:
    proc, replay_studio = reproduce.launch_studio(LAUNCH, reproduce.isolated_home())
    try:
        replayed = reproduce.reproduce(HFREC, out / "replay", replay_studio)
    finally:
        proc.terminate()
    print(replayed.verdict)
    print(replayed.text())

# %% [markdown]
# Read the verdict with its reasons. `identical` means the pixels are equal to the record's
# digest. `different` on a machine with the same GPU and the same Studio and Core builds is a bug or
# a hidden input, and the report says so. Across different GPUs, no tolerance has been measured, so
# none is claimed: pass `tolerance=` yourself if you decide what counts as close enough.
# The same checks are on the command line: `hfcore record verify`, `hfcore record preflight`.
