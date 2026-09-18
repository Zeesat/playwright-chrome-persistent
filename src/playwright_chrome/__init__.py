"""
Playwright Chrome Persistent Profile Manager.
Provides persistent context launcher for Google Chrome with anti-detection flags
and preserved DPAPI credential storage for uninterrupted Google account sessions.
"""

from .core import (
    launch_persistent_browser,
    get_profile_dir,
    is_profile_initialized,
    clean_locks,
)

__all__ = [
    "launch_persistent_browser",
    "get_profile_dir",
    "is_profile_initialized",
    "clean_locks",
]
