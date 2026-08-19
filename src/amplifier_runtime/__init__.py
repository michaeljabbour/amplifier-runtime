"""UI-neutral session runtime for Amplifier clients."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("amplifier-runtime")
except PackageNotFoundError:
    __version__ = "0.1.7"

__all__ = ["__version__"]
