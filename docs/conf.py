"""Sphinx configuration for the pyHelioFITS manual.

Build:  python3 -m sphinx -W -b html docs docs/_build/html
"""
import re
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
release = version = re.search(r'^version\s*=\s*"([^"]+)"', (_root / "pyproject.toml").read_text(), re.M).group(1)

project = "pyHelioFITS"
author = "Gilly, NWRA"
copyright = "the pyHelioFITS authors; MPL 2.0"
extensions = ["myst_parser", "sphinx_copybutton"]
myst_enable_extensions = ["colon_fence", "deflist", "substitution"]
myst_substitutions = {"release": release}
myst_heading_anchors = 3
source_suffix = {".md": "markdown"}
root_doc = "index"
exclude_patterns = ["_build", "requirements.txt"]
html_theme = "furo"
html_title = f"pyHelioFITS {release}"
html_theme_options = {
    "navigation_with_keys": True,
    "source_repository": "https://github.com/GillySpace27/pyHelioFITS/",
    "source_branch": "main",
    "source_directory": "docs/",
    "light_css_variables": {"color-brand-primary": "#c2410c", "color-brand-content": "#b45309"},
    "dark_css_variables": {"color-brand-primary": "#fb923c", "color-brand-content": "#fbbf24"},
}
copybutton_prompt_text = r"\$ "
copybutton_prompt_is_regexp = True
latex_engine = "xelatex"
latex_documents = [("index", "pyHelioFITS-Manual.tex", "pyHelioFITS Manual", author, "manual")]
