"""
Playwright Chrome Persistent MCP Server.
Provides FastMCP tools for controlling persistent Chrome sessions over CDP or direct context.
"""

import atexit
import json
import os
import queue
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

from mcp.server.fastmcp import FastMCP
from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from .core import (
    DEFAULT_CDP_HOST,
    DEFAULT_CDP_PORT,
    clean_locks,
    connect_cdp,
    ensure_host,
    get_profile_dir,
    is_cdp_active,
    launch_persistent_browser,
    stop_host,
)

SNAPSHOT_JS_SCRIPT = """() => {
    function truncate(s, maxLen = 80) {
        if (!s) return "";
        const clean = s.trim().replace(/\\s+/g, " ");
        return clean.length > maxLen ? clean.substring(0, maxLen) + "..." : clean;
    }

    const headings = [];
    document.querySelectorAll("h1, h2, h3").forEach(el => {
        const text = truncate(el.innerText || el.textContent);
        if (text) {
            headings.push(`${el.tagName.toLowerCase()}: ${text}`);
        }
    });

    const buttons = [];
    document.querySelectorAll("button, input[type='button'], input[type='submit'], [role='button']").forEach(el => {
        const text = truncate(el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("title") || "");
        if (text) {
            buttons.push(text);
        }
    });

    const inputs = [];
    document.querySelectorAll("input:not([type='hidden']):not([type='button']):not([type='submit']), textarea, select").forEach(el => {
        const name = el.name || el.id || "";
        const placeholder = el.placeholder || "";
        const label = el.getAttribute("aria-label") || "";
        const type = el.type || el.tagName.toLowerCase();
        const val = truncate(el.value || "");
        let desc = `[${type}]`;
        if (name) desc += ` name="${name}"`;
        if (placeholder) desc += ` placeholder="${placeholder}"`;
        if (label) desc += ` aria-label="${label}"`;
        if (val) desc += ` value="${val}"`;
        inputs.push(desc);
    });

    const links = [];
    document.querySelectorAll("a[href]").forEach(el => {
        const text = truncate(el.innerText || el.textContent || el.getAttribute("aria-label") || "");
        const href = el.getAttribute("href") || "";
        if (text && href && !href.startsWith("javascript:")) {
            links.push(`${text} -> ${truncate(href, 60)}`);
        }
    });

    return {
        headings: headings.slice(0, 30),
        buttons: buttons.slice(0, 30),
        inputs: inputs.slice(0, 30),
        links: links.slice(0, 30),
    };
}"""


def format_snapshot(title: str, url: str, data: Dict[str, Any]) -> str:
    """Format extracted DOM elements into clean structured text."""
    lines = [
        f"Title: {title}",
        f"URL: {url}",
        "",
        "--- Headings ---",
    ]
    headings: List[str] = data.get("headings", [])
    if headings:
        for h in headings:
            lines.append(f"  {h}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("--- Buttons ---")
    buttons: List[str] = data.get("buttons", [])
    if buttons:
        for b in buttons:
            lines.append(f"  [button] {b}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("--- Inputs ---")
    inputs: List[str] = data.get("inputs", [])
    if inputs:
        for inp in inputs:
            lines.append(f"  {inp}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("--- Links ---")
    links: List[str] = data.get("links", [])
    if links:
        for link in links:
            lines.append(f"  {link}")
    else:
        lines.append("  (none)")

    return "\n".join(lines)


class BrowserSessionManager:
    """
    In-memory session manager for persistent Chrome browser automation.

    Maintains single persistent context, automatically attaching to active CDP
    on port 9222 or launching persistent Chrome directly. All Playwright calls
    are routed to a dedicated worker thread to preserve greenlet thread affinity
    and avoid asyncio event loop collisions.
    """

    def __init__(
        self,
        profile: Optional[str] = None,
        cdp_port: int = DEFAULT_CDP_PORT,
        cdp_host: str = DEFAULT_CDP_HOST,
        headless: bool = False,
    ):
        self.profile = profile
        self.cdp_port = cdp_port
        self.cdp_host = cdp_host
        self.headless = headless

        self.p: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.is_cdp: bool = False

        self._queue: queue.Queue = queue.Queue()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="PersistentChromeWorkerThread",
            daemon=True,
        )
        self._worker_thread.start()

    def _worker_loop(self) -> None:
        """Dedicated execution loop for all Playwright sync operations."""
        while True:
            item = self._queue.get()
            if item is None:
                break
            fn, args, kwargs, res_q = item
            try:
                result = fn(*args, **kwargs)
                res_q.put((True, result))
            except Exception as exc:
                res_q.put((False, exc))

    def _run(self, fn, *args, **kwargs):
        """Execute callable on dedicated Playwright worker thread."""
        if threading.current_thread() == self._worker_thread:
            return fn(*args, **kwargs)
        res_q: queue.Queue = queue.Queue()
        self._queue.put((fn, args, kwargs, res_q))
        ok, val = res_q.get()
        if ok:
            return val
        raise val

    def _ensure_session_worker(self) -> Page:
        """Ensure active browser session and return valid page on worker thread."""
        # 1. Verify health of existing session
        if self.context is not None:
            try:
                if self.page is None or self.page.is_closed():
                    open_pages = [p for p in self.context.pages if not p.is_closed()]
                    if open_pages:
                        self.page = open_pages[-1]
                    else:
                        self.page = self.context.new_page()
                _ = self.page.url
                return self.page
            except Exception:
                self._close_worker()

        # 2. Check if Chrome host is running on CDP port 9222
        if not is_cdp_active(port=self.cdp_port, host=self.cdp_host):
            ensure_host(
                port=self.cdp_port,
                host=self.cdp_host,
                profile=self.profile,
                headless=self.headless,
            )

        if is_cdp_active(port=self.cdp_port, host=self.cdp_host):
            self.p, self.browser, self.context, self.page = connect_cdp(
                port=self.cdp_port, host=self.cdp_host
            )
            self.is_cdp = True
        else:
            self.p, self.context, self.page = launch_persistent_browser(
                headless=self.headless,
                user_data_dir=self.profile,
                cdp_port=self.cdp_port,
                cdp_host=self.cdp_host,
            )
            self.browser = None
            self.is_cdp = False

        return self.page

    def ensure_session(self) -> Page:
        """Public thread-safe session initialization."""
        return self._run(self._ensure_session_worker)

    def _open_worker(self, url: str) -> str:
        page = self._ensure_session_worker()
        page.goto(url, wait_until="load", timeout=30000)
        title = page.title()
        current_url = page.url
        return f"Successfully opened {url} (Title: '{title}', Current URL: '{current_url}')"

    def open(self, url: str = "https://myaccount.google.com/") -> str:
        return self._run(self._open_worker, url)

    def _snapshot_worker(self) -> str:
        page = self._ensure_session_worker()
        title = page.title()
        url = page.url
        data = page.evaluate(SNAPSHOT_JS_SCRIPT)
        return format_snapshot(title, url, data)

    def snapshot(self) -> str:
        return self._run(self._snapshot_worker)

    def _click_worker(self, selector: str) -> str:
        page = self._ensure_session_worker()
        page.click(selector, timeout=10000)
        try:
            page.wait_for_load_state("load", timeout=5000)
        except Exception:
            pass
        title = page.title()
        url = page.url
        return f"Successfully clicked '{selector}' (Title: '{title}', Current URL: '{url}')"

    def click(self, selector: str) -> str:
        return self._run(self._click_worker, selector)

    def _type_worker(self, selector: str, text: str, submit: bool = False) -> str:
        page = self._ensure_session_worker()
        page.fill(selector, text, timeout=10000)
        if submit:
            page.press(selector, "Enter", timeout=5000)
            try:
                page.wait_for_load_state("load", timeout=5000)
            except Exception:
                pass
            return f"Successfully typed into '{selector}' and submitted."
        return f"Successfully typed into '{selector}'."

    def type_text(self, selector: str, text: str, submit: bool = False) -> str:
        return self._run(self._type_worker, selector, text, submit)

    def _screenshot_worker(self, filename: Optional[str] = None) -> str:
        page = self._ensure_session_worker()
        if filename and filename.strip():
            target_path = Path(filename).resolve()
        else:
            shots_dir = Path.cwd() / "screenshots"
            target_path = shots_dir / f"screenshot_{int(time.time())}.png"

        target_path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target_path), full_page=False)
        return f"Screenshot saved successfully to: {target_path}"

    def screenshot(self, filename: Optional[str] = None) -> str:
        return self._run(self._screenshot_worker, filename)

    def _status_worker(self) -> str:
        profile_path = get_profile_dir(self.profile)
        cdp_active = is_cdp_active(port=self.cdp_port, host=self.cdp_host)
        session_active = self.context is not None

        if session_active and self.page is not None:
            try:
                if not self.page.is_closed():
                    current_url = self.page.url
                    page_title = self.page.title()
                else:
                    current_url = "No active page"
                    page_title = "No active page"
            except Exception:
                current_url = "No active page"
                page_title = "No active page"
        else:
            current_url = "No active page"
            page_title = "No active page"

        cookies_count = 0
        if session_active and self.context is not None:
            try:
                cookies_count = len(self.context.cookies())
            except Exception:
                cookies_count = 0

        status_data = {
            "profile_path": profile_path,
            "cdp_active": cdp_active,
            "session_active": session_active,
            "connection_mode": "cdp" if self.is_cdp else ("direct" if session_active else "none"),
            "current_url": current_url,
            "page_title": page_title,
            "cookies_count": cookies_count,
        }
        return json.dumps(status_data, indent=2)

    def status(self) -> str:
        return self._run(self._status_worker)

    def _close_worker(self) -> str:
        mode = "cdp" if self.is_cdp else ("direct" if self.context is not None else "none")
        if self.is_cdp:
            if self.browser is not None:
                try:
                    self.browser.close()
                except Exception:
                    pass
                self.browser = None
            if self.p is not None:
                try:
                    self.p.stop()
                except Exception:
                    pass
                self.p = None
        else:
            if self.context is not None:
                try:
                    self.context.close()
                except Exception:
                    pass
                self.context = None
            if self.p is not None:
                try:
                    self.p.stop()
                except Exception:
                    pass
                self.p = None
            clean_locks(self.profile)

        self.page = None
        self.context = None
        self.browser = None
        self.is_cdp = False
        return f"Persistent Chrome session closed cleanly (mode was {mode})."

    def close(self) -> str:
        return self._run(self._close_worker)

    def shutdown(self) -> None:
        """Cleanly close Playwright session and stop background worker thread."""
        try:
            self.close()
        except Exception:
            pass
        if self._worker_thread.is_alive():
            self._queue.put(None)


# Initialize FastMCP server and in-memory session manager
mcp = FastMCP("persistent-chrome", dependencies=["playwright"])
session_manager = BrowserSessionManager()
atexit.register(session_manager.shutdown)


@mcp.tool()
def persistent_chrome_open(url: str = "https://myaccount.google.com/") -> str:
    """Open a URL in persistent Chrome browser session."""
    try:
        return session_manager.open(url)
    except Exception as exc:
        return f"Error opening URL '{url}': {type(exc).__name__}: {str(exc)}"


@mcp.tool()
def persistent_chrome_snapshot() -> str:
    """Extract page title, URL, headings, buttons, links, and input fields as clean structured text."""
    try:
        return session_manager.snapshot()
    except Exception as exc:
        return f"Error capturing snapshot: {type(exc).__name__}: {str(exc)}"


@mcp.tool()
def persistent_chrome_click(selector: str) -> str:
    """Click matching element and wait for load state."""
    try:
        return session_manager.click(selector)
    except Exception as exc:
        return f"Error clicking selector '{selector}': {type(exc).__name__}: {str(exc)}"


@mcp.tool()
def persistent_chrome_type(selector: str, text: str, submit: bool = False) -> str:
    """Fill text into matching element, optionally pressing Enter to submit."""
    try:
        return session_manager.type_text(selector, text, submit=submit)
    except Exception as exc:
        return f"Error typing into selector '{selector}': {type(exc).__name__}: {str(exc)}"


@mcp.tool()
def persistent_chrome_screenshot(filename: Optional[str] = None) -> str:
    """Save screenshot to file and return path."""
    try:
        return session_manager.screenshot(filename=filename)
    except Exception as exc:
        return f"Error capturing screenshot: {type(exc).__name__}: {str(exc)}"


@mcp.tool()
def persistent_chrome_status() -> str:
    """Report profile path, active URL, cookie count, and CDP state."""
    try:
        return session_manager.status()
    except Exception as exc:
        return f"Error retrieving status: {type(exc).__name__}: {str(exc)}"


@mcp.tool()
def persistent_chrome_close() -> str:
    """Close the in-memory context cleanly."""
    try:
        return session_manager.close()
    except Exception as exc:
        return f"Error closing session: {type(exc).__name__}: {str(exc)}"


@mcp.tool()
def persistent_chrome_ensure_host(headless: bool = False) -> str:
    """Ensure persistent Chrome host daemon is running in background without terminal stalling.

    Spawns or verifies the detached Chrome host daemon on 127.0.0.1:9222 using the persistent profile.
    AI agents must call this tool directly instead of executing 'playwright-chrome ensure-host' in terminal.
    """
    try:
        profile_path = get_profile_dir(session_manager.profile)
        active = ensure_host(
            port=session_manager.cdp_port,
            host=session_manager.cdp_host,
            profile=session_manager.profile,
            headless=headless,
        )
        if active:
            return (
                f"Persistent Chrome host daemon is READY and listening on "
                f"http://{session_manager.cdp_host}:{session_manager.cdp_port} "
                f"(profile: {profile_path}, headless={headless})"
            )
        return f"Failed to start persistent Chrome host daemon on port {session_manager.cdp_port}."
    except Exception as exc:
        return f"Error ensuring host: {type(exc).__name__}: {str(exc)}"


@mcp.tool()
def persistent_chrome_stop_host() -> str:
    """Gracefully stop the persistent Chrome host daemon and clean locks without terminal stalling."""
    try:
        session_manager.close()
        stopped = stop_host(
            port=session_manager.cdp_port,
            profile=session_manager.profile,
        )
        if stopped:
            return "Persistent Chrome host daemon stopped successfully and locks cleaned."
        return "Host daemon process stopped, but CDP port may still be in use."
    except Exception as exc:
        return f"Error stopping host: {type(exc).__name__}: {str(exc)}"


@mcp.tool()
def persistent_chrome_clean_locks() -> str:
    """Clean stale profile lockfiles (SingletonLock) after crash or forced termination."""
    try:
        profile_path = clean_locks(session_manager.profile)
        return f"Cleaned stale lockfiles in profile: {profile_path}"
    except Exception as exc:
        return f"Error cleaning locks: {type(exc).__name__}: {str(exc)}"


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
