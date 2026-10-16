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
DB_NAME = "heliad.sqlite"


def app_support_dir() -> Path:
    """Per-user directory for Heliad's config, following each platform's convention."""
    override = os.environ.get("HELIAD_HOME")
    if override:
        return Path(override).expanduser()
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Heliad"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Heliad"
    return Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "heliad"


def default_library_root() -> Path:
    return Path.home() / "HeliadLibrary"


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
    """Explicit root wins, then HELIAD_LIBRARY, then the config file, then the default."""
    if library_root is not None:
        return Config(Path(library_root).expanduser().resolve())
    env = os.environ.get("HELIAD_LIBRARY")
    if env:
        return Config(Path(env).expanduser().resolve())
    path = config_path()
    if path.exists():
        data = json.loads(path.read_text())
        return Config(Path(data["library_root"]).expanduser())
    return Config(default_library_root())


def write(cfg: Config, schema_version: int) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg.to_json(schema_version), indent=2) + "\n")
    tmp.replace(path)
    return path
