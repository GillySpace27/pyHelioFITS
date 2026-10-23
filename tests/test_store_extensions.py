"""Stored files get a FITS suffix (scope: cache-extensions-scope.md, option A): at ingest,
and for files already kept through `heliad fix-extensions`."""

import gzip
import io
import os
import sys

import pytest
from conftest import aia_header, write_fits

from heliad import cli, library, views
from heliad.library import Library, clone_or_copy, fits_name
from heliad.rpc import Server


def _bare(src, name, wave, gz=False):
    plain = write_fits(src / f"plain-{name}.fits", aia_header(wave=wave, date=f"2025-09-21T16:0{wave % 10}:00"))
    p = src / name
    p.write_bytes(gzip.compress(plain.read_bytes()) if gz else plain.read_bytes())
    plain.unlink()
    return p


def test_fits_name_by_content(src):
    gz = _bare(src, "0123abcd", 171, gz=True)
    plain = _bare(src, "4567ef01", 193)
    other = src / "notes"
    other.write_bytes(b"hello")
    assert fits_name(gz.name, gz) == "0123abcd.fits.gz"
    assert fits_name(plain.name, plain) == "4567ef01.fits"
    assert fits_name(other.name, other) == "notes"
    assert fits_name("x.fits", plain) == "x.fits"
    assert fits_name("x.FTS.gz", gz) == "x.FTS.gz"
    assert fits_name("x.gz", gz) == "x.gz"  # a .gz name is left as it is


def test_ingest_stores_a_bare_digest_with_a_suffix(lib, src):
    a = lib.ingest(_bare(src, "0123abcd", 171, gz=True))
    b = lib.ingest(_bare(src, "4567ef01", 193))
    assert a.relpath.endswith("/0123abcd.fits.gz") and b.relpath.endswith("/4567ef01.fits")
    names = {r["original_name"] for r in lib.reader.execute("SELECT original_name FROM files")}
    assert names == {"0123abcd", "4567ef01"}  # where it came from is kept as it was


@pytest.fixture
def legacy(lib, src, monkeypatch):
    """A library stored the way Heliad did before: bare names kept bare."""
    monkeypatch.setattr(library, "fits_name", lambda name, path: name)
    rows = [lib.ingest(_bare(src, "0123abcd", 171, gz=True)), lib.ingest(_bare(src, "4567ef01", 193))]
    monkeypatch.undo()
    views.create(lib, "event", start="2025-09-21T00:00", end="2025-09-22T00:00")
    views.materialize(lib, "event")
    return rows


def _relpaths(lib):
    return {r["id"]: r["relpath"] for r in lib.reader.execute("SELECT id, relpath FROM files")}


def test_fix_extensions_renames_records_aliases_and_links(lib, legacy):
    before = _relpaths(lib)
    dry = lib.fix_extensions(dry_run=True)
    assert (dry["candidates"], dry["fits"], dry["fits_gz"], dry["renamed"]) == (2, 1, 1, 0)
    assert _relpaths(lib) == before and all(lib.abspath(p).is_file() for p in before.values())

    out = lib.fix_extensions()
    assert (out["renamed"], out["fits"], out["fits_gz"], out["failed"]) == (2, 1, 1, [])
    after = _relpaths(lib)
    for fid, old in before.items():
        new = after[fid]
        assert new.endswith((".fits", ".fits.gz")) and new.startswith(old)
        assert lib.abspath(new).is_file() and not lib.abspath(new).is_symlink()
        assert lib.abspath(new).stat().st_mode & 0o222 == 0  # still read-only
        alias = lib.reader.execute("SELECT file_id FROM file_aliases WHERE old_relpath = ?", (old,)).fetchone()
        assert alias["file_id"] == fid
        if out["links_made"]:
            link = lib.abspath(old)
            assert link.is_symlink() and not os.path.isabs(os.readlink(link))
            assert link.read_bytes() == lib.abspath(new).read_bytes()
    assert lib.verify()["missing"] == [] and lib.verify()["altered"] == []
    assert out["views_rebuilt"] == ["event"]
    assert {m["relpath"] for m in views.members(lib, "event")} == set(after.values())

    again = lib.fix_extensions()  # idempotent
    assert again["candidates"] == 0 and again["renamed"] == 0 and _relpaths(lib) == after


def test_fix_extensions_finishes_a_run_stopped_after_the_rename(lib, legacy):
    (fid, old), *_ = _relpaths(lib).items()
    p = lib.abspath(old)
    renamed = p.with_name(fits_name(p.name, p))
    os.rename(p, renamed)  # the rename happened; the index never heard of it
    out = lib.fix_extensions()
    assert out["missing"] == 0 and out["renamed"] == 2
    assert _relpaths(lib)[fid] == renamed.relative_to(lib.root).as_posix()


def test_fix_extensions_keeps_the_alias_without_symlinks(lib, legacy, monkeypatch):
    def refuse(*a, **k):
        raise OSError("symlinks not allowed here")
    monkeypatch.setattr(library.os, "symlink", refuse)
    out = lib.fix_extensions()
    assert out["renamed"] == 2 and out["links_made"] == 0 and out["links_skipped"] == 2
    assert lib.reader.execute("SELECT COUNT(*) FROM file_aliases").fetchone()[0] == 2
    monkeypatch.undo()
    later = lib.fix_extensions()  # where symlinks work again, the links are made then
    assert later["renamed"] == 0 and later["links_made"] == 2


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="open files are read from /proc")
def test_fix_extensions_skips_a_file_held_open(lib, legacy):
    (fid, old), *_ = _relpaths(lib).items()
    with open(lib.abspath(old), "rb"):
        out = lib.fix_extensions()
    assert out["open_check"] == "proc" and out["open"] == 1 and out["renamed"] == 1
    assert _relpaths(lib)[fid] == old
    assert lib.fix_extensions()["renamed"] == 1  # closed now, so the next run does it


def test_delete_after_fix_extensions_removes_the_alias_and_link(lib, legacy):
    lib.fix_extensions()
    fid, _ = next(iter(_relpaths(lib).items()))
    (old,) = [r[0] for r in lib.reader.execute("SELECT old_relpath FROM file_aliases WHERE file_id = ?", (fid,))]
    out = lib.delete(file_ids=[fid], dry_run=False)
    assert out["files"] == 1 and out["unlink_failed"] == []
    assert not lib.abspath(old).is_symlink()
    assert lib.reader.execute("SELECT COUNT(*) FROM file_aliases WHERE file_id = ?", (fid,)).fetchone()[0] == 0


def test_rpc_fix_extensions_is_a_dry_run_unless_asked(lib, legacy):
    m = Server(lib, out=io.StringIO()).methods
    assert m["library.fix_extensions"]()["dry_run"] is True
    assert lib.reader.execute("SELECT COUNT(*) FROM file_aliases").fetchone()[0] == 0
    assert m["library.fix_extensions"](dry_run=False)["renamed"] == 2


def test_cli_fix_extensions_prints_counts(tmp_path, src, monkeypatch, capsys):
    monkeypatch.setenv("HELIAD_HOME", str(tmp_path / "appsupport"))
    root = tmp_path / "lib2"
    monkeypatch.setattr(library, "fits_name", lambda name, path: name)
    with Library.open(root) as lib:
        lib.ingest(_bare(src, "89abcdef", 304))
    monkeypatch.undo()
    assert cli.main(["--library", str(root), "fix-extensions", "--dry-run"]) == 0
    err = capsys.readouterr().err
    assert "1 stored files without a FITS suffix: would rename 1 (1 .fits, 0 .fits.gz)" in err
    assert cli.main(["--library", str(root), "fix-extensions"]) == 0
    assert "renamed 1" in capsys.readouterr().err


def test_clone_or_copy_reflinks_on_linux_and_copies_otherwise(tmp_path, monkeypatch):
    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * 5000)
    out = clone_or_copy(src, tmp_path / "b.bin")
    assert out in ("clone", "copy") and (tmp_path / "b.bin").read_bytes() == src.read_bytes()
    if not sys.platform.startswith("linux"):
        return
    import fcntl

    def refuse(fd, op, arg):
        assert op == library._FICLONE
        raise OSError(95, "Operation not supported")
    monkeypatch.setattr(fcntl, "ioctl", refuse)
    assert clone_or_copy(src, tmp_path / "c.bin") == "copy"
    assert (tmp_path / "c.bin").read_bytes() == src.read_bytes()

    def clone(fd, op, arg):  # stands in for a filesystem that shares blocks
        os.write(fd, os.pread(arg, 1 << 20, 0))
    monkeypatch.setattr(fcntl, "ioctl", clone)
    assert clone_or_copy(src, tmp_path / "d.bin") == "clone"
    assert (tmp_path / "d.bin").read_bytes() == src.read_bytes()
