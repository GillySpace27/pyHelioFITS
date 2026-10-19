"""python -m heliad: the same as the heliad command (the app runs its bundled Python this way)."""

import sys

from .cli import main

sys.exit(main())
