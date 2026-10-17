"""Build the optional C++ trade walk, `candlebench._enginecore`.

Optional because the engine runs without it: a machine with no C++ compiler
installs the pure-Python walk, which produces the same trades more slowly.
`-ffp-contract=off` keeps the compiler from fusing a multiply and an add, which
would round differently from Python and break bit-for-bit agreement.
"""

import sys

from pybind11.setup_helpers import Pybind11Extension
from setuptools import setup

# MSVC does not contract by default and does not accept the GCC/Clang flag.
FLAGS = [] if sys.platform == "win32" else ["-O2", "-ffp-contract=off"]

setup(
    ext_modules=[
        Pybind11Extension(
            "candlebench._enginecore",
            ["candlebench/_enginecore.cpp"],
            cxx_std=17,
            extra_compile_args=FLAGS,
            optional=True,
        )
    ]
)
