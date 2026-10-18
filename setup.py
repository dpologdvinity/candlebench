"""Build the optional C++ trade walk, `candlebench._enginecore`.

Optional because the engine runs without it: a machine with no C++ compiler,
or a build without pybind11, installs the pure-Python walk, which produces the
same trades more slowly.

The flags keep the compiler from rounding differently from Python, which would
break bit-for-bit agreement: `-ffp-contract=off` stops it fusing a multiply and
an add, and `-fno-fast-math` overrides a `-ffast-math` in the user's CFLAGS,
since extension flags come after them. MSVC contracts only under /fp:fast and
accepts neither flag, so they are chosen by compiler, not by platform: MinGW
on Windows is GCC.
"""

import warnings

from setuptools import setup
from setuptools.command.build_ext import build_ext

GCC_FLAGS = ["-O2", "-ffp-contract=off", "-fno-fast-math"]


class BuildExt(build_ext):
    def build_extensions(self):
        if self.compiler.compiler_type != "msvc":
            for ext in self.extensions:
                ext.extra_compile_args += GCC_FLAGS
        super().build_extensions()


try:
    from pybind11.setup_helpers import Pybind11Extension
except ImportError:
    warnings.warn("pybind11 is not installed; building without the C++ trade walk")
    extensions = []
else:
    extensions = [
        Pybind11Extension(
            "candlebench._enginecore",
            ["candlebench/_enginecore.cpp"],
            cxx_std=17,
            optional=True,
        )
    ]

setup(ext_modules=extensions, cmdclass={"build_ext": BuildExt})
