import os
import sys
import glob
from pathlib import Path
from typing import Tuple, Optional
from playwright.sync_api import sync_playwright, Playwright, BrowserContext, Page

DEFAULT_PROFILE_DIR_NAME = ".chrome_profile"

ANTI_DETECTION_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-infobars",
    "--start-maximized",
]

# Critical: removing --use-mock-keychain ensures Chromium uses native Windows DPAPI
# to encrypt and decrypt cookies and credentials without resetting session state.
IGNORE_DEFAULT_ARGS = [
    "--enable-automation",
    "--use-mock-keychain",
]


def get_default_profile_dir() -> str:
    """
    Resolve default profile directory path.
    Priority:
    1. PLAYWRIGHT_CHROME_PROFILE environment variable if set.
    2. .chrome_profile directory in current working directory or repository root.
    """
    env_dir = os.environ.get("PLAYWRIGHT_CHROME_PROFILE")
    if env_dir:
        return os.path.abspath(env_dir)

    # Walk up to find project root or use cwd
    current = Path.cwd()
    return str(current / DEFAULT_PROFILE_DIR_NAME)


def get_profile_dir(custom_path: Optional[str] = None) -> str:
    """Return normalized absolute profile directory."""
    if custom_path:
        return os.path.abspath(custom_path)
    return get_default_profile_dir()


def is_profile_initialized(user_data_dir: Optional[str] = None) -> bool:
    """Check whether profile directory contains initialized Chrome profile data."""
    target_dir = Path(get_profile_dir(user_data_dir))
    default_sub = target_dir / "Default"
    local_state = target_dir / "Local State"
    return target_dir.is_dir() and (default_sub.is_dir() or local_state.is_file())


def clean_locks(user_data_dir: Optional[str] = None) -> int:
    """
    Remove stale SingletonLock or lockfile files if Chrome crashed or was terminated.
    Returns count of removed lock files.
    """
    target = Path(get_profile_dir(user_data_dir))
    if not target.exists():
        return 0

    removed = 0
    patterns = ["SingletonLock", "lockfile", "SingletonSocket", "SingletonCookie"]
    for pattern in patterns:
        for p in target.glob(pattern):
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def launch_persistent_browser(
    headless: bool = False,
    user_data_dir: Optional[str] = None,
    channel: str = "chrome",
    extra_args: Optional[list] = None,
) -> Tuple[Playwright, BrowserContext, Page]:
    """
    Launch a persistent Chromium/Chrome browser session.
    
    Args:
        headless: Whether to run without visible browser window.
        user_data_dir: Custom directory path for profile storage.
        channel: Browser distribution to use ('chrome', 'msedge', or None for bundled chromium).
        extra_args: Additional command-line flags for Chromium.

    Returns:
        Tuple of (Playwright, BrowserContext, Page).
    """
    profile_path = get_profile_dir(user_data_dir)
    os.makedirs(profile_path, exist_ok=True)
    clean_locks(profile_path)

    args = list(ANTI_DETECTION_ARGS)
    if extra_args:
        args.extend(extra_args)

    p = sync_playwright().start()

    context = p.chromium.launch_persistent_context(
        user_data_dir=profile_path,
        channel=channel,
        headless=headless,
        no_viewport=True,
        args=args,
        ignore_default_args=IGNORE_DEFAULT_ARGS,
    )

    page = context.pages[0] if context.pages else context.new_page()
    return p, context, page
