"""python -m pyheliofits.core: the same as the hfcore command (the apps run their bundled Python
this way)."""

import sys

from .cli import main

sys.exit(main())
