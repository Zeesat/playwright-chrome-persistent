"""
Playwright Chrome Persistent Profile Manager.
Provides persistent context launcher for Google Chrome with anti-detection flags
and preserved DPAPI credential storage for uninterrupted Google account sessions.
"""

from .core import (
    DEFAULT_CDP_PORT,
    DEFAULT_CDP_HOST,
    launch_persistent_browser,
    get_profile_dir,
    is_profile_initialized,
    clean_locks,
    is_cdp_active,
    connect_cdp,
    ensure_host,
    stop_host,
    spawn_chrome_host,
    find_chrome_executable,
)
from . import mcp_server

__all__ = [
    "DEFAULT_CDP_PORT",
    "DEFAULT_CDP_HOST",
    "launch_persistent_browser",
    "get_profile_dir",
    "is_profile_initialized",
    "clean_locks",
    "is_cdp_active",
    "connect_cdp",
    "ensure_host",
    "stop_host",
    "spawn_chrome_host",
    "find_chrome_executable",
    "mcp_server",
]
