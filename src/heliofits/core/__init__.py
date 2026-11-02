"""HelioFITS Core: find solar data, download it and keep it in one library."""

from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("heliofits")  # pyproject.toml is the one place the version is written
except PackageNotFoundError:
    for _old in ("heliofits-core", "heliad"):  # installs made before the renames
        try:
            __version__ = _version(_old)
            break
        except PackageNotFoundError:
            continue
    else:  # a checkout that is not installed
        __version__ = "0+unknown"
