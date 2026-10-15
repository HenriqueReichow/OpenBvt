"""opensonar — open SDK for P900 and M900 sonars."""
from .son import SonFile, SonError, OpenSonarError
from .image import rtheta, xy
from .sdk import open, ApiError, Stream  # noqa: A004 — opensonar.open is the public entry point

__version__ = "4.1.0"
