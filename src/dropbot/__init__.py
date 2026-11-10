"""Headless Twitch Drops and Channel Points farmer."""

import os
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dropbot")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "dev"

# The git commit the image was built from (set by CI as a Docker build arg), else "".
REVISION = os.environ.get("DROPBOT_REVISION", "")[:7]


def version_label() -> str:
    """e.g. "v0.5.0 (bffa3dc)", or "v0.5.0" outside a CI-built image."""
    return f"v{__version__}" + (f" ({REVISION})" if REVISION else "")
