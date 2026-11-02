"""Turn a percent-format Python file (cells split by `# %%`, markdown by `# %% [markdown]`) into a
notebook. The .py files in notebooks/ are the source; run `python tools/pct2ipynb.py` to rebuild."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def cells(text: str) -> list[tuple[str, str]]:
    out, kind, buf = [], None, []
    for line in text.splitlines():
        if line.startswith("# %%"):
            if kind is not None:
                out.append((kind, "\n".join(buf).strip("\n")))
            kind, buf = ("markdown" if "[markdown]" in line else "code"), []
        elif kind is not None:
            buf.append(line)
    if kind is not None:
        out.append((kind, "\n".join(buf).strip("\n")))
    return out


def notebook(text: str) -> dict:
    nb_cells = []
    for kind, body in cells(text):
        if kind == "markdown":
            body = "\n".join(l[2:] if l.startswith("# ") else l.lstrip("#") for l in body.splitlines())
            nb_cells.append({"cell_type": "markdown", "metadata": {}, "source": body.splitlines(True)})
        else:
            nb_cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                             "source": body.splitlines(True)})
    return {"cells": nb_cells, "nbformat": 4, "nbformat_minor": 5,
            "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}}}


def main() -> None:
    here = Path(__file__).resolve().parent.parent / "notebooks"
    for src in sorted(here.glob("*.py")):
        (here / (src.stem + ".ipynb")).write_text(json.dumps(notebook(src.read_text("utf-8")), indent=1) + "\n", "utf-8")
        print("wrote", src.stem + ".ipynb", file=sys.stderr)


if __name__ == "__main__":
    main()
