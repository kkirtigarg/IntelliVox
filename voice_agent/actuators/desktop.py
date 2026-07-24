"""Platform-specific desktop actuator factory."""
from __future__ import annotations

import sys

from .base import Actuator


def build_desktop_actuator():
    """Return the real GUI actuator for the current OS."""
    if sys.platform == "win32":
        from .windows_gui import WindowsGuiActuator
        return WindowsGuiActuator()
    if sys.platform.startswith("linux"):
        from .linux_gui import LinuxGuiActuator
        return LinuxGuiActuator()
    raise RuntimeError(
        f"No real desktop actuator for platform {sys.platform!r}. "
        "Use --demo mode, or run on Windows or Linux."
    )


def desktop_platform_label() -> str:
    if sys.platform == "win32":
        return "Windows"
    if sys.platform.startswith("linux"):
        return "Linux"
    return sys.platform
