"""HelioFITS Core: find solar data, download it and keep it in one library."""

from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("heliofits-core")  # pyproject.toml is the one place the version is written
except PackageNotFoundError:
    try:
        __version__ = _version("heliad")  # an install made before the rename
    except PackageNotFoundError:  # a checkout that is not installed
        __version__ = "0+unknown"
