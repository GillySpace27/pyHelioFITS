"""The import name of HelioFITS Core from before it became `heliofits.core` (the engine of the
`heliofits` package). Kept so programs written for it keep working: HelioFITS Studio and the Mac
app run `python -m heliofits_core`, and `import heliofits_core` gives the heliofits.core package
itself, `heliofits_core.<module>` its modules. `heliad` is the name from before that."""

import importlib
import importlib.abc
import importlib.util
import sys

import heliofits.core as _core

_OLD, _NEW = __name__, "heliofits.core"


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """heliofits_core.X is heliofits.core.X: the same module object, never a second copy."""

    def find_spec(self, name, path=None, target=None):
        # __main__ is left to the normal finders, which look in heliofits.core (this package's
        # path), so `python -m heliofits_core` runs heliofits/core/__main__.py as code, as runpy needs.
        if name.startswith(_OLD + ".") and name != _OLD + ".__main__":
            return importlib.util.spec_from_loader(name, self)
        return None

    def create_module(self, spec):
        return importlib.import_module(_NEW + spec.name[len(_OLD):])

    def exec_module(self, module):
        pass


sys.meta_path.insert(0, _Alias())
sys.modules[_OLD] = _core
