"""From a picture to its FITS: what the library and the archives hold for the frames of a
layer that shows pictures (Helioviewer's JPEG 2000 frames in Studio).

Helioviewer serves pictures made from FITS files, and each picture carries the FITS header
it was made from. The caller sends those headers, one per frame it shows. Each is read with
the rules ingest uses (fitsmeta.from_header), so a frame is described as its FITS file would
be once in the library. Then, per frame: the library already keeps a FITS file for it
(in_library, with the file), it was set aside (rejected, never fetched again), it is missing,
or its header does not say enough to look it up (unreadable).

A picture and a FITS file are one frame when they agree on observatory, instrument, detector
and wavelength (within the library's passband tolerance) and lie within a time tolerance:
the dataset's (upgrade.dataset_tolerance, at the frames' own spacing) when the library has
the dataset, else half the frames' spacing, clamped as upgrade.tolerance clamps it. Level
and product are not compared: a picture's LEVEL is what it was made from (a quicklook or a
level 1.5 product, say), and the archive's FITS of the same observation may be another level.

The missing frames of each group get one fetch, at the frames' own cadence (one record per
spacing of the frames shown, so a layer streamed at one frame per 30 minutes is not
upgraded to every 12 s exposure): dataset.upgrade's query when the library has the dataset
(only frames it lacks are fetched), else a Fido query inferred from the headers. A dry run
returns the plan; otherwise each fetch is queued as a job.
"""

from __future__ import annotations

import re
from dataclasses import asdict, replace

from . import fitsmeta, upgrade
from .library import WAVELENGTH_BAND_TOLERANCE, Library
from .query import Query

MAX_FRAMES = 10000

# HMI's two products share one wavelength (6173 Å) and differ only in CONTENT. The Physobs
# names are sunpy's VSO ones; that VSO accepts them for HMI is checked on a real search,
# not here (the build container cannot reach an archive).
HMI_PHYSOBS = {"MAGNETOGRAM": "LOS_magnetic_field", "CONTINUUM INTENSITY": "intensity",
               "DOPPLERGRAM": "LOS_velocity"}


def _same(a, b) -> bool:
    return fitsmeta._norm(a) == fitsmeta._norm(b)


def _same_wave(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= WAVELENGTH_BAND_TOLERANCE * max(a, b)


def _is_hmi(meta: fitsmeta.FitsMeta) -> bool:
    # HMI writes its camera after the name (INSTRUME = 'HMI_FRONT2', 'HMI_SIDE1').
    return _archive_instrument_of(meta.instrument) == "HMI"


def _archive_instrument_of(instrument: str | None) -> str | None:
    """The instrument as an archive names it: HMI's camera suffix left out, so a picture from
    HMI_FRONT2 and a file from HMI_SIDE1 are both HMI."""
    return "HMI" if fitsmeta._norm(instrument).split("-")[0] == "hmi" else instrument


def _archive_instrument(meta: fitsmeta.FitsMeta) -> str | None:
    return _archive_instrument_of(meta.instrument)


def _picture_header(header: dict) -> dict:
    """The header as ingest's rules can date it. LASCO pictures write the date with slashes in
    DATE_OBS and the time in TIME_OBS (2026/09/29 and 15:12:33.xxx); ingest reads ISO dates and
    TIME-OBS. Only this copy changes; the rules ingest files by are untouched."""
    out = dict(header)
    for key in ("DATE-OBS", "DATE_OBS"):
        value = out.get(key)
        if isinstance(value, str) and "T" not in value and re.fullmatch(r"\s*\d{4}/\d{2}/\d{2}\s*", value):
            out[key] = value.strip().replace("/", "-")
    if "TIME-OBS" not in out and out.get("TIME_OBS"):
        out["TIME-OBS"] = out["TIME_OBS"]
    return out


def _physobs(header: dict, meta: fitsmeta.FitsMeta) -> str | None:
    if not _is_hmi(meta):
        return None
    content = fitsmeta._clean(header.get("CONTENT"))
    return HMI_PHYSOBS.get(content.upper()) if content else None


def _identity(header: dict, meta: fitsmeta.FitsMeta) -> tuple:
    wave = None if meta.wavelength is None else round(meta.wavelength, 1)
    return (fitsmeta._norm(meta.observatory), fitsmeta._norm(meta.instrument),
            fitsmeta._norm(meta.detector), wave, _physobs(header, meta))


def _datasets(conn, meta: fitsmeta.FitsMeta) -> list[dict]:
    """The library's datasets of this picture's observatory, instrument, detector and passband,
    most observations first."""
    rows = conn.execute(
        "SELECT d.dataset_key, d.observatory, d.instrument, d.detector, d.wavelength, d.label, "
        "COUNT(o.id) AS n FROM datasets d LEFT JOIN observations o ON o.dataset_id = d.id "
        "GROUP BY d.id ORDER BY n DESC, d.dataset_key").fetchall()
    return [dict(r) for r in rows
            if _same(r["observatory"], meta.observatory)
            and _same(_archive_instrument_of(r["instrument"]), _archive_instrument(meta))
            and _same(r["detector"], meta.detector) and _same_wave(r["wavelength"], meta.wavelength)]


def _kept(lib: Library, keys: list[str], lo: float, hi: float) -> tuple[list, list]:
    """(current files, rejected times) of these datasets between lo and hi, each sorted by time."""
    files, rejected = [], []
    for key in keys:
        for row in lib.current_files(dataset_key=key, start=upgrade._iso(lo), end=upgrade._iso(hi),
                                     include_rejected=True):
            t = upgrade.seconds(row["date_obs"])
            if t is None:
                continue
            (rejected if row.get("rejected") else files).append((t, row))
    files.sort(key=lambda x: x[0])
    rejected.sort(key=lambda x: x[0])
    return files, rejected


def _nearest(kept: list, t: float, tol: float):
    best = None
    for kt, row in kept:
        if abs(kt - t) <= tol and (best is None or abs(kt - t) < abs(best[0] - t)):
            best = (kt, row)
    return best


def plan(lib: Library, headers: list, sample_s: float | None = None) -> dict:
    """What the library holds for each frame, and the fetch that would get the rest."""
    if not isinstance(headers, list) or not headers:
        raise ValueError("headers must be a non-empty list of FITS headers, one per frame")
    if len(headers) > MAX_FRAMES:
        raise ValueError(f"at most {MAX_FRAMES} frames at once; {len(headers)} given")
    conn = lib.reader
    frames: list[dict] = []
    groups: dict[tuple, dict] = {}
    for i, header in enumerate(headers):
        if not isinstance(header, dict):
            raise ValueError(f"header {i} is not an object of FITS keywords")
        header = _picture_header(header)
        meta = fitsmeta.from_header(header, "")
        t = upgrade.seconds(meta.date_obs)
        frame = {"frame": i, "date_obs": meta.date_obs, "group": None, "status": "unreadable",
                 "reason": None}
        frames.append(frame)
        if not meta.instrument or t is None:
            frame["reason"] = "the header names no instrument" if not meta.instrument else \
                "the header gives no observation time"
            continue
        ident = _identity(header, meta)
        g = groups.get(ident)
        if g is None:
            g = groups[ident] = {"group": len(groups), "meta": meta, "physobs": _physobs(header, meta),
                                 "frames": []}
        frame["group"] = g["group"]
        g["frames"].append((t, frame))

    out_groups = []
    for g in groups.values():
        meta = g["meta"]
        times = sorted(t for t, _ in g["frames"])
        spacing = upgrade.median_spacing(times)
        target = sample_s or spacing
        datasets = _datasets(conn, meta)
        keys = [d["dataset_key"] for d in datasets]
        tol = (upgrade.dataset_tolerance(conn, keys[0], target) if keys
               else upgrade.tolerance(spacing, sample_s))
        files, rejected = _kept(lib, keys, times[0] - tol, times[-1] + tol)
        missing = []
        for t, frame in g["frames"]:
            hit = _nearest(files, t, tol)
            if hit is not None:
                row = hit[1]
                frame.update(status="in_library", file_id=row["file_id"], relpath=row["relpath"],
                             path=str(lib.abspath(row["relpath"])), dataset_key=row["dataset_key"],
                             observation_id=row["observation_id"], fits_date_obs=row["date_obs"])
            elif _nearest(rejected, t, tol) is not None:
                row = _nearest(rejected, t, tol)[1]
                frame.update(status="rejected", dataset_key=row["dataset_key"],
                             observation_id=row["observation_id"],
                             reason=f"set aside in the library: {row['rejected']}")
            else:
                frame["status"] = "missing"
                missing.append(t)
        counts = {s: sum(1 for _, f in g["frames"] if f["status"] == s)
                  for s in ("in_library", "rejected", "missing")}
        entry = {"group": g["group"], "label": replace(meta, level=None, product=None).label,
                 "observatory": meta.observatory,
                 "instrument": meta.instrument, "detector": meta.detector,
                 "wavelength": meta.wavelength, "physobs": g["physobs"], "count": len(times),
                 "counts": counts, "spacing_s": spacing, "tolerance_s": tol,
                 "datasets": keys, "fetch": None, "reason": None}
        if missing:
            entry.update(_fetch(lib, meta, g["physobs"], keys, missing, tol, target))
        out_groups.append(entry)

    total = {s: sum(1 for f in frames if f["status"] == s)
             for s in ("in_library", "rejected", "missing", "unreadable")}
    return {"frames": frames, "groups": out_groups, "counts": total,
            "complete": total["missing"] == 0 and total["unreadable"] == 0}


def _fetch(lib: Library, meta, physobs, keys, missing, tol, target) -> dict:
    lo, hi = missing[0] - tol, missing[-1] + tol
    sample = target if len(missing) > 1 else None
    if keys:
        base = upgrade.dataset_query(lib, keys[0])
        q = replace(Query.from_json(base["query"]), start=upgrade._iso(lo), end=upgrade._iso(hi),
                    sample_s=sample)
        return {"fetch": {"query": asdict(q), "source": base["source"],
                          "options": {"dataset_key": keys[0], "missing_only": True, "tolerance_s": tol},
                          "inferred": base["inferred"],
                          "basis": f"the library's dataset {keys[0]}: {base['basis']}"}}
    source = upgrade._sunpy_source(meta.observatory)
    q = Query(start=upgrade._iso(lo), end=upgrade._iso(hi), instrument=_archive_instrument(meta),
              wavelength=meta.wavelength, source=source, detector=meta.detector,
              physobs=physobs, sample_s=sample)
    basis = ("inferred from the pictures' FITS headers (instrument, detector and wavelength as "
             "ingest reads them; level left out, since a picture's level is not the archive's)")
    if meta.observatory and source is None:
        basis += f"; observatory {meta.observatory!r} is not a sunpy Source here, so source is left null"
    if physobs:
        basis += f"; physobs {physobs} from the header's CONTENT"
    return {"fetch": {"query": asdict(q), "source": "fido", "options": {"missing_only": False},
                      "inferred": True, "basis": basis}}


def upgrade_layer(lib: Library, runner, headers: list, sample_s: float | None = None,
                  dry_run: bool = True) -> dict:
    """plan(), and unless dry_run, one queued job per group with missing frames (its `job`)."""
    result = plan(lib, headers, sample_s)
    result["dry_run"] = dry_run
    if dry_run:
        return result
    for g in result["groups"]:
        f = g["fetch"]
        if f is not None:
            g["job"] = runner.enqueue(Query.from_json(f["query"]), f["source"], options=f["options"])
    return result
