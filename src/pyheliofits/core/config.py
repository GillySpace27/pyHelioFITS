"""Where the library lives, and the small config file other programs read to find it.

The config file is the handshake with every other reader (HelioFITS Studio, the
Mac GUI, scripts): it names the library root and the schema version, and every
path stored in the index is relative to that root.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "config.json"
SETTINGS_NAME = "settings.json"
DB_NAME = "hfcore.sqlite"


def env(name: str) -> str | None:
    """HFCORE_<name> when it is set."""
    return os.environ.get(f"HFCORE_{name}") or None


def _support_dir() -> Path:
    """This platform's per-user settings folder for HelioFITS Core."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "HelioFITS Core"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "HelioFITS Core"
    return Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "hfcore"


def app_support_dir() -> Path:
    """Per-user directory for the config and settings: HFCORE_HOME when set, else the platform's folder."""
    override = env("HOME")
    if override:
        return Path(override).expanduser()
    return _support_dir()


def default_library_root() -> Path:
    """~/HelioFITSLibrary."""
    return Path.home() / "HelioFITSLibrary"


@dataclass
class Config:
    library_root: Path

    @property
    def store_dir(self) -> Path:
        return self.library_root / "store"

    @property
    def views_dir(self) -> Path:
        return self.library_root / "views"

    @property
    def staging_dir(self) -> Path:
        return self.library_root / ".staging"

    @property
    def db_path(self) -> Path:
        """The index, hfcore.sqlite."""
        return self.library_root / DB_NAME

    def to_json(self, schema_version: int) -> dict:
        return {
            "library_root": str(self.library_root),
            "db_path": str(self.db_path),
            "schema_version": schema_version,
        }


def config_path() -> Path:
    return app_support_dir() / CONFIG_NAME


def load(library_root: str | os.PathLike | None = None) -> Config:
    """Explicit root wins, then HFCORE_LIBRARY, then the config file, then the default."""
    if library_root is not None:
        return Config(Path(library_root).expanduser().resolve())
    named = env("LIBRARY")
    if named:
        return Config(Path(named).expanduser().resolve())
    path = config_path()
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return Config(Path(data["library_root"]).expanduser())
    return Config(default_library_root())


def exists() -> bool:
    return config_path().exists()


def write(cfg: Config, schema_version: int) -> Path:
    """Point every reader at this library. Only `hfcore init` (or a first run with no
    config at all) calls this, so a one-off `--library` never redirects Studio."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg.to_json(schema_version), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


# ---- settings ------------------------------------------------------------
# Per-user preferences that are not part of the reader handshake, such as the
# email address JSOC requires for exports. Environment variables win.

SETTING_ENV = {"jsoc_email": "JSOC_EMAIL"}  # HFCORE_JSOC_EMAIL


def settings_path() -> Path:
    return app_support_dir() / SETTINGS_NAME


def load_settings() -> dict:
    path = settings_path()
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for key, name in SETTING_ENV.items():
        if env(name):
            data[key] = env(name)
    return data


def save_setting(key: str, value: str | None) -> dict:
    path = settings_path()
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return data


# ---- where a library should not live ---------------------------------------

_SYNCED_PARTS = ("Library/Mobile Documents", "Library/CloudStorage", "Dropbox", "Google Drive",
                 "OneDrive", "Nextcloud", "Box Sync")
_NETWORK_FS = {"smbfs", "nfs", "nfs4", "afpfs", "webdav", "cifs", "smb3", "sshfs", "fuse.sshfs",
               "osxfuse", "macfuse"}


def _filesystem_type(path: Path) -> str | None:
    """The type of the filesystem holding path, from the mount table; None if unknown."""
    import subprocess

    path = path.resolve()
    while not path.exists():
        path = path.parent
    mounts: list[tuple[str, str]] = []
    try:
        if sys.platform.startswith("linux"):
            for line in Path("/proc/mounts").read_text().splitlines():
                parts = line.split()
                if len(parts) >= 3:
                    mounts.append((parts[1], parts[2]))
        elif sys.platform == "darwin":
            out = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():  # "/dev/disk3s5 on /System/Volumes/Data (apfs, local, ...)"
                if " on " in line and " (" in line:
                    point = line.split(" on ", 1)[1].rsplit(" (", 1)
                    mounts.append((point[0], point[1].split(",")[0].strip("()")))
    except (OSError, subprocess.SubprocessError):
        return None
    best = None
    for point, kind in mounts:
        if str(path) == point or str(path).startswith(point.rstrip("/") + "/"):
            if best is None or len(point) > len(best[0]):
                best = (point, kind)
    return best[1] if best else None


def location_warnings(root: Path) -> list[str]:
    """Reasons a library root is a bad idea. SQLite's WAL mode and file locks are not
    reliable on network or sync-service folders, and iCloud evicts files it syncs."""
    text = str(Path(root).expanduser())
    warnings = []
    for part in _SYNCED_PARTS:
        if part in text:
            warnings.append(f"{text} is inside a sync service folder ({part}); the index can be "
                            "corrupted and files evicted. Use a plain local folder such as ~/HelioFITSLibrary.")
            break
    home = str(Path.home())
    if sys.platform == "darwin" and (text.startswith(home + "/Documents") or text.startswith(home + "/Desktop")):
        warnings.append(f"{text} is under Documents or Desktop, which iCloud syncs when "
                        "'Desktop & Documents Folders' is on. Prefer ~/HelioFITSLibrary.")
    kind = _filesystem_type(Path(text))
    if kind and kind.lower() in _NETWORK_FS:
        warnings.append(f"{text} is on a network filesystem ({kind}); SQLite locking is not reliable there.")
    return warnings
