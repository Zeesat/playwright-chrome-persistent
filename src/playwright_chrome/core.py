import os
import sys
import glob
import json
import time
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Tuple, Optional
from playwright.sync_api import sync_playwright, Playwright, Browser, BrowserContext, Page

DEFAULT_PROFILE_DIR_NAME = ".chrome_profile"
DEFAULT_CDP_PORT = 9222
DEFAULT_CDP_HOST = "127.0.0.1"

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
    cdp_port: Optional[int] = DEFAULT_CDP_PORT,
    cdp_host: str = DEFAULT_CDP_HOST,
) -> Tuple[Playwright, BrowserContext, Page]:
    """
    Launch a persistent Chromium/Chrome browser session.
    
    Args:
        headless: Whether to run without visible browser window.
        user_data_dir: Custom directory path for profile storage.
        channel: Browser distribution to use ('chrome', 'msedge', or None for bundled chromium).
        extra_args: Additional command-line flags for Chromium.
        cdp_port: Remote debugging port for DevTools Protocol (CDP), or None to disable.
        cdp_host: Remote debugging host address (default: "127.0.0.1").

    Returns:
        Tuple of (Playwright, BrowserContext, Page).
    """
    if cdp_port is not None:
        if not isinstance(cdp_port, int) or isinstance(cdp_port, bool):
            raise TypeError(f"cdp_port must be an integer or None, got {type(cdp_port).__name__}")
        if not (1 <= cdp_port <= 65535):
            raise ValueError(f"cdp_port must be between 1 and 65535, got {cdp_port}")
    if not isinstance(cdp_host, str) or not cdp_host:
        raise TypeError(f"cdp_host must be a non-empty string, got {type(cdp_host).__name__}")

    profile_path = get_profile_dir(user_data_dir)
    os.makedirs(profile_path, exist_ok=True)
    clean_locks(profile_path)

    args = list(ANTI_DETECTION_ARGS)
    if extra_args:
        args.extend(extra_args)
    if cdp_port is not None:
        args.append(f"--remote-debugging-port={cdp_port}")
        args.append(f"--remote-debugging-address={cdp_host}")

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


def is_cdp_active(
    port: int = DEFAULT_CDP_PORT,
    host: str = DEFAULT_CDP_HOST,
    timeout: float = 1.0,
) -> bool:
    """
    Check if a Chrome DevTools Protocol (CDP) endpoint is active and responsive.

    Queries `http://{host}:{port}/json/version` and verifies HTTP status 200
    and that the parsed JSON response contains the 'Browser' key.

    Args:
        port: Remote debugging port to check (default: DEFAULT_CDP_PORT, 9222).
        host: Remote debugging host address (default: DEFAULT_CDP_HOST, "127.0.0.1").
        timeout: Network timeout in seconds (default: 1.0).

    Returns:
        True if the CDP endpoint is responsive and returns valid browser metadata,
        False otherwise (connection refused, timeout, invalid JSON, or non-200 status).
    """
    if not isinstance(port, int) or isinstance(port, bool):
        raise TypeError(f"port must be an integer, got {type(port).__name__}")
    if not (1 <= port <= 65535):
        raise ValueError(f"port must be between 1 and 65535, got {port}")
    if not isinstance(host, str) or not host.strip():
        raise TypeError(f"host must be a non-empty string, got {type(host).__name__}")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError(f"timeout must be a positive number, got {timeout}")

    url = f"http://{host}:{port}/json/version"
    req = urllib.request.Request(url, headers={"User-Agent": "playwright-chrome"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = getattr(response, "status", getattr(response, "code", None))
            if status != 200:
                return False
            payload = response.read().decode("utf-8", errors="replace")
            data = json.loads(payload)
            return isinstance(data, dict) and "Browser" in data
    except Exception:
        return False


def connect_cdp(
    port: int = DEFAULT_CDP_PORT,
    host: str = DEFAULT_CDP_HOST,
) -> Tuple[Playwright, Browser, BrowserContext, Page]:
    """
    Connect to an existing Chrome browser instance exposing Chrome DevTools Protocol (CDP).

    Attaches a Playwright client session to the active browser over CDP without
    launching a new browser process or altering persistent profile directory locks.
    Closing the returned `browser` instance (`browser.close()`) closes the CDP client
    connection without terminating the persistent Chrome host process.

    Args:
        port: Remote debugging port for CDP (default: DEFAULT_CDP_PORT, 9222).
        host: Remote debugging host address (default: DEFAULT_CDP_HOST, "127.0.0.1").

    Returns:
        Tuple of (Playwright, Browser, BrowserContext, Page).
    """
    if not isinstance(port, int) or isinstance(port, bool):
        raise TypeError(f"port must be an integer, got {type(port).__name__}")
    if not (1 <= port <= 65535):
        raise ValueError(f"port must be between 1 and 65535, got {port}")
    if not isinstance(host, str) or not host.strip():
        raise TypeError(f"host must be a non-empty string, got {type(host).__name__}")

    p = sync_playwright().start()
    endpoint_url = f"http://{host}:{port}"
    try:
        browser = p.chromium.connect_over_cdp(endpoint_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        return p, browser, context, page
    except Exception:
        p.stop()
        raise


def find_chrome_executable(channel: str = "chrome") -> Optional[str]:
    import shutil

    if channel == "msedge":
        edge_candidates = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
        for c in edge_candidates:
            if os.path.exists(c):
                return c
        return shutil.which("msedge") or shutil.which("microsoft-edge")

    chrome_candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in chrome_candidates:
        if os.path.exists(c):
            return c
    return (
        shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chrome")
        or shutil.which("chromium")
    )


def spawn_chrome_host(
    port: int = DEFAULT_CDP_PORT,
    host: str = DEFAULT_CDP_HOST,
    profile: Optional[str] = None,
    channel: str = "chrome",
    headless: bool = False,
    extra_args: Optional[list] = None,
    url: Optional[str] = None,
) -> Optional[int]:
    profile_path = get_profile_dir(profile)
    os.makedirs(profile_path, exist_ok=True)
    clean_locks(profile_path)

    exe = find_chrome_executable(channel=channel)
    if not exe:
        return None

    args = [
        exe,
        f"--user-data-dir={profile_path}",
        f"--remote-debugging-port={port}",
        f"--remote-debugging-address={host}",
    ]
    args.extend(ANTI_DETECTION_ARGS)
    if headless:
        args.append("--headless=new")
    if extra_args:
        args.extend(extra_args)
    if url:
        args.append(url)

    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )

    proc = subprocess.Popen(
        args,
        creationflags=creationflags,
        close_fds=(sys.platform != "win32"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )

    meta_file = Path(profile_path) / ".cdp_host.json"
    meta_data = {
        "pid": proc.pid,
        "port": port,
        "host": host,
        "profile": profile_path,
        "channel": channel,
        "started_at": time.time(),
    }
    try:
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta_data, f)
    except Exception:
        pass

    return proc.pid


def ensure_host(
    port: int = DEFAULT_CDP_PORT,
    host: str = DEFAULT_CDP_HOST,
    profile: Optional[str] = None,
    channel: str = "chrome",
    headless: bool = False,
    timeout: float = 6.0,
) -> bool:
    if is_cdp_active(port=port, host=host):
        return True

    spawn_chrome_host(
        port=port,
        host=host,
        profile=profile,
        channel=channel,
        headless=headless,
    )

    start_time = time.time()
    while time.time() - start_time < timeout:
        if is_cdp_active(port=port, host=host):
            return True
        time.sleep(0.2)

    return is_cdp_active(port=port, host=host)


def stop_host(
    port: int = DEFAULT_CDP_PORT,
    profile: Optional[str] = None,
) -> bool:
    profile_path = get_profile_dir(profile)
    meta_file = Path(profile_path) / ".cdp_host.json"

    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            pid = meta.get("pid")
            if pid:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid), "/T"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    import signal
                    os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        try:
            meta_file.unlink()
        except OSError:
            pass

    if sys.platform == "win32":
        try:
            cmd = f'Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like "*{profile_path}*" }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}'
            subprocess.run(["powershell", "-NoProfile", "-Command", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    clean_locks(profile_path)
    return not is_cdp_active(port=port)


