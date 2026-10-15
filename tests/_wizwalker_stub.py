"""A stand-in for WizWalker so the unit tests run where it can't be installed.

WizWalker is Windows-only (it hooks the game's memory), so on Linux CI the
bot's modules failed to import. `install()` puts a fake `wizwalker` package
in `sys.modules`: `XYZ`, `calculate_perfect_yaw` and `Keycode` behave like
the real ones (copied from Deimos' wizwalker); every other name is an empty
placeholder class, enough for imports, subclassing and type hints. Tests
that drive the game itself still need Windows.
"""

import importlib.abc
import importlib.machinery
import math
import os
import sys
import types
from pathlib import Path


class XYZ:
    def __init__(self, x: float, y: float, z: float):
        self.x = x
        self.y = y
        self.z = z

    def __sub__(self, other):
        return self.distance(other)

    def __str__(self):
        return f"<XYZ ({self.x}, {self.y}, {self.z})>"

    def __repr__(self):
        return str(self)

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def distance(self, other):
        if not isinstance(other, type(self)):
            raise ValueError(
                f"Can only calculate distance between instances of {type(self)} not {type(other)}"
            )
        return math.dist((self.x, self.y), (other.x, other.y))

    def yaw(self, other):
        if not isinstance(other, type(self)):
            raise ValueError(
                f"Can only calculate yaw between instances of {type(self)} not {type(other)}"
            )
        return calculate_perfect_yaw(self, other)

    def relative_yaw(self, *, x: float = None, y: float = None):
        if x is None:
            x = self.x
        if y is None:
            y = self.y
        return self.yaw(type(self)(x, y, self.z))


def calculate_perfect_yaw(current_xyz: XYZ, target_xyz: XYZ) -> float:
    target_line = math.dist((current_xyz.x, current_xyz.y), (target_xyz.x, target_xyz.y))
    origin_line = math.dist((current_xyz.x, current_xyz.y), (current_xyz.x, current_xyz.y - 1))
    target_to_origin_line = math.dist(
        (target_xyz.x, target_xyz.y), (current_xyz.x, current_xyz.y - 1)
    )
    if 1.0 - abs(origin_line) > 0.0 or 1.0 - abs(target_line) > 0.0:
        return 0
    target_angle = math.acos(
        (pow(target_line, 2) + pow(origin_line, 2) - pow(target_to_origin_line, 2))
        / (2 * origin_line * target_line)
    )
    if target_xyz.x > current_xyz.x:
        return math.radians(360 - math.degrees(target_angle))
    return target_angle


class _KeycodeMeta(type):
    # Keycode.W, Keycode.ESC...: one distinct, stable member per name.
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        member = cls._members.get(name)
        if member is None:
            member = cls._members[name] = object.__new__(cls)
            member.name = name
            member.value = len(cls._members)
        return member


class Keycode(metaclass=_KeycodeMeta):
    _members: dict = {}

    def __repr__(self):
        return f"<Keycode.{self.name}>"


# Where the real wizwalker package's files would be (see WIZWALKER_SRC in CI).
DATA_ROOT = Path(os.environ.get("WIZWALKER_SRC", Path(__file__).parent / "_no_wizwalker"))

_REAL = {"XYZ": XYZ, "Keycode": Keycode, "calculate_perfect_yaw": calculate_perfect_yaw}


class _StubModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if name in _REAL:
            return _REAL[name]
        placeholder = type(name, (), {"__module__": self.__name__})
        setattr(self, name, placeholder)
        return placeholder


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path, target=None):
        if fullname == "wizwalker" or fullname.startswith("wizwalker."):
            return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        module = _StubModule(spec.name)
        module.__path__ = []
        # travel_data finds WizSprinter's map files next to wizwalker's __init__.
        module.__file__ = str(DATA_ROOT / spec.name.replace(".", "/") / "__init__.py")
        return module

    def exec_module(self, module):
        pass


def install() -> None:
    sys.meta_path.insert(0, _Finder())
