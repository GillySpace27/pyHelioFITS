"""The old import name of HelioFITS Core, kept so programs written for it keep working
(HelioFITS Studio 0.8.x and the first Mac app run `python -m heliad`): `import heliad`
gives the heliofits_core package itself, and `heliad.<module>` its modules."""

import importlib
import importlib.abc
import importlib.util
import sys

import heliofits_core

_OLD, _NEW = __name__, "heliofits_core"


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """heliad.X is heliofits_core.X: the same module object, never a second copy."""

    def find_spec(self, name, path=None, target=None):
        # __main__ is left to the normal finders, which look in heliofits_core (this package's
        # path), so `python -m heliad` runs heliofits_core/__main__.py as code, as runpy needs.
        if name.startswith(_OLD + ".") and name != _OLD + ".__main__":
            return importlib.util.spec_from_loader(name, self)
        return None

    def create_module(self, spec):
        return importlib.import_module(_NEW + spec.name[len(_OLD):])

    def exec_module(self, module):
        pass


sys.meta_path.insert(0, _Alias())
sys.modules[_OLD] = heliofits_core
