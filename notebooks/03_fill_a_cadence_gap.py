# %% [markdown]
# # 3. Find and fill a cadence gap in the cache, then reload the layer
#
# `cache` is HelioFITS Core in this process, so it works with Studio closed. Filling a gap
# downloads from the archive, so the dry-run steps come first.

# %%
from heliofits import cache, studio

# Pick a dataset from the cache (the first one here; choose yours).
datasets = cache.library_datasets()
key = datasets[0]["key"]
print(key, len(datasets), "datasets in the cache")

# %% [markdown]
# Compare the cache's cadence with the archive's. Nothing is downloaded.

# %%
cad = cache.dataset_cadences(dataset_key=key)
print("cache  :", cad["library"])
print("archive:", cad["archive"])
print(len(cad["missing"]), "archive times missing from the cache")

# %% [markdown]
# A dry run shows the query that would run. Then run it for real (`dry_run=False`); the job is
# carried to the end here and comes back finished.

# %%
plan = cache.dataset_upgrade(dataset_key=key, dry_run=True)
print(plan["query"])

# %%
if cad["missing"]:
    job = cache.dataset_upgrade(dataset_key=key, dry_run=False)
    print(job["status"], job.get("result"))

# %% [markdown]
# Studio keeps the frames it loaded. Reload the layer so it sees the new files.

# %%
layers = studio.layer_list()["layers"]
for layer in layers:
    if layer["source"] == "fits-library":
        print("reloaded", layer["id"], studio.layer_reload(layer_id=layer["id"])["frames"], "frames")
