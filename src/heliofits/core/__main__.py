"""python -m heliofits.core: the same as the hfcore command (the apps run their bundled Python
this way). python -m heliad, the old name, runs this too."""

import sys

from .cli import main

sys.exit(main())
