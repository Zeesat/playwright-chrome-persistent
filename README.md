# Playwright Persistent Chrome Profile Manager

A robust, self-contained automation profile manager and CLI utility for Playwright on Windows, macOS, and Linux. It maintains persistent Google account authentication across test and automation runs without session invalidation or cookie resets.

> **⚡ AI QUICK USE (NO NEED TO READ .PY SOURCE FILES)**:
> - **Open URL in persistent browser (Non-blocking CLI)**: `playwright-chrome open "<url>"` (auto-reuses CDP on 9222 or auto-spawns background host daemon)
> - **Host daemon lifecycle**: `playwright-chrome ensure-host` and `playwright-chrome stop-host`
> - **Interactive Google login**: `playwright-chrome login`
> - **Attach to running browser in Python**:
>   ```python
>   from playwright_chrome import connect_cdp
>   p, browser, context, page = connect_cdp(9222)
>   page.goto("<url>")
>   browser.close()  # Detaches client only, keeps host Chrome alive
>   p.stop()
>   ```
> - **Launch fresh persistent Chrome in Python**:
>   ```python
>   from playwright_chrome import launch_persistent_browser
>   p, context, page = launch_persistent_browser(headless=False)
>   ```
> - **Status & Lock cleanup**: `playwright-chrome status` and `playwright-chrome clean-locks`

---

## Technical Problem & Architecture

When running automated browser sessions with Playwright against Google services (Gmail, Accounts, Rewards, Workspace), standard automation workflows frequently suffer from immediate logouts or bot detection:

1. **App-Bound Encryption (Chrome 127+)**:
   Google Chrome on Windows uses system-level App-Bound Encryption for cookie stores located in the default User Data directory (`%LOCALAPPDATA%\Google\Chrome\User Data`). Directly copying or accessing default user profiles causes decryption failures, which triggers Chrome to purge all cookies on startup.

2. **DevTools Remote Debugging Restriction (Chrome 130+)**:
   Google Chrome blocks remote debugging connections attached directly to its default User Data directory, throwing:
   `DevTools remote debugging requires a non-default data directory. Specify this using --user-data-dir.`

3. **Playwright Mock Keychain Default**:
   By default, Playwright launches Chromium instances with `--use-mock-keychain` and `--password-store=basic`. This prevents the browser from utilizing the Windows Data Protection API (DPAPI), causing stored credentials to be discarded between sessions.

### How This Solution Solves It
- Allocates an isolated, non-default user data directory (`.chrome_profile/`).
- Drops `--use-mock-keychain` and `--enable-automation` via `ignore_default_args`, allowing native DPAPI cookie encryption to persist safely.
- Employs anti-detection command line flags (`--disable-blink-features=AutomationControlled`, `--disable-infobars`, `--start-maximized`) to pass client-side integrity and bot verifications.
- Isolates automation credentials entirely from the user's daily desktop Chrome instance.

---

## Prerequisites

- Python 3.10 or newer.
- Google Chrome installed in standard installation directory (or Chromium/Microsoft Edge).
- Playwright Python package (`playwright>=1.40.0`).

---

## Installation

### 1. Clone or Copy the Repository
```bash
git clone <repo-url> playwright-chrome-persistent
cd playwright-chrome-persistent
```

### 2. Install Dependencies
Install dependencies directly or in a virtual environment:

```bash
pip install -r requirements.txt
```

### 3. Install as a CLI Tool (Optional but Recommended)
Install in editable mode to register the `playwright-chrome` command globally in your terminal:

```bash
pip install -e .
```

Alternatively, use the provided batch or shell wrappers:
- Windows Command Prompt: `playwright-chrome.cmd <command>`
- Windows PowerShell: `.\bin\playwright-chrome.ps1 <command>`
- Direct Python: `python run_cli.py <command>`

---

## Step-by-Step Usage Guide

### Step 1: Initial Google Account Login (Run Once)
Launch the interactive login wizard:

```bash
playwright-chrome login
```

1. A visible Chrome window will open to `https://accounts.google.com/`.
2. Complete your Google login manually (including 2FA if enabled).
3. Return to the terminal and press `[ENTER]`.
4. The browser closes cleanly, and your full session cookies, local storage, and authentication tokens are encrypted and saved into `.chrome_profile/`.

### Step 2: Verify Profile Status
Inspect the profile directory to confirm cookies were captured:

```bash
playwright-chrome status
```

Output:
```text
Profile Directory: D:\VSC\Tesinng\playwright-chrome-persistent\.chrome_profile
Exists:            True
Initialized:       True
Disk Usage:        18.39 MB
Total Cookies:     52
Google Cookies:    41
```

### Step 3: Test Persistent Authentication
Launch the browser against any authenticated Google portal:

```bash
playwright-chrome open https://myaccount.google.com/
```

Or run headless in automation pipelines:

```bash
playwright-chrome open https://myaccount.google.com/ --headless
```

Your account will be recognized and stay logged in.

---

## Python API Integration

You can integrate this persistent context into your own automation and web scraping scripts:

```python
from playwright_chrome import launch_persistent_browser

# Launch visible or headless persistent browser
playwright_instance, context, page = launch_persistent_browser(headless=False)

# Navigate to target service (already authenticated)
page.goto("https://myaccount.google.com/")
print("Page Title:", page.title())

# Perform your automation tasks here
# ...

# Cleanly disconnect
context.close()
playwright_instance.stop()
```

### Custom Profile Directory
To specify a custom profile path:

```python
from playwright_chrome import launch_persistent_browser

p, context, page = launch_persistent_browser(
    headless=False,
    user_data_dir=r"D:\custom\automation_profile"
)
```

Or set the environment variable:

```bash
set PLAYWRIGHT_CHROME_PROFILE=D:\custom\automation_profile
```

---

# Remote Debugging & CDP Integration (Port 9222)

Every persistent Chrome instance launched by this package automatically opens a Chrome DevTools Protocol (CDP) remote debugging port. By default, it binds to loopback address `127.0.0.1` on port `9222`.

This allows external scripts, Playwright MCP tools, and AI agents to attach to the running browser without profile lock collisions.

### Direct Playwright Connection
External scripts or AI tools can connect to the running session directly through Playwright:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://myaccount.google.com/")
```

### Python Helpers
The package exports high-level helper functions for CDP connectivity:

```python
from playwright_chrome import connect_cdp, is_cdp_active

# Check if a browser session is accepting CDP connections on port 9222
if is_cdp_active(port=9222):
    # Attach over CDP without causing profile lock collisions
    p, browser, context, page = connect_cdp(port=9222)
    page.goto("https://myaccount.google.com/")
    
    # Detach cleanly (leaves host browser running)
    browser.close()
    p.stop()
```

### CLI Support and Seamless Tab Reuse
All CLI subcommands support `--port PORT` (or global `--port PORT` option) to target custom CDP ports.

When executing `playwright-chrome open <url>` against an already-running session:
1. The CLI detects the active CDP service at `127.0.0.1:9222` (or specified `--port`).
2. It reuses an open tab or opens a new tab seamlessly inside the running browser.
3. The page navigates to the requested URL, and the CLI detaches gracefully without interrupting the host browser process.

---

# OpenCode MCP Server Integration

The package includes a built-in FastMCP server (`persistent-chrome-mcp`) that exposes 7 high-level browser tools directly to OpenCode and AI agents.

### Tool Reference Table

| Tool Name | Parameters | Description |
|---|---|---|
| `persistent_chrome_open` | `url: str`, `headless: bool = False` | Navigates the persistent Chrome instance to the specified URL. Automatically connects to CDP on 9222 or launches Chrome. |
| `persistent_chrome_snapshot` | None | Takes a structural text snapshot of the active web page, returning text and interactive element maps. |
| `persistent_chrome_click` | `selector: str` | Clicks an interactive element on the page using a CSS or text selector. |
| `persistent_chrome_type` | `selector: str`, `text: str` | Clears and types specified text into an input or textarea element. |
| `persistent_chrome_screenshot` | `path: str = "screenshot.png"` | Captures a PNG screenshot of the current viewport or full page. |
| `persistent_chrome_status` | None | Returns background host status, profile path, cookie count, and active page URL. |
| `persistent_chrome_close` | None | Detaches the MCP client cleanly from Chrome while keeping the background host session alive. |

### Registering in `opencode.json`

To register the native MCP server in OpenCode, add it to your `opencode.json` or `~/.config/opencode/opencode.json`:

```json
{
  "mcpServers": {
    "persistent-chrome": {
      "command": "persistent-chrome-mcp",
      "args": []
    }
  }
}
```

### Advantages Over Shell Execution

1. **Zero Shell Subprocess Stalling**: Running CLI commands directly in agent subshells can block or hang on stdout/stderr handles; MCP tool calls execute natively via JSON-RPC.
2. **Dedicated Thread & Session Isolation**: The MCP server manages Playwright calls on a dedicated background worker thread, eliminating greenlet thread-switching crashes and asyncio event loop conflicts.
3. **Automatic Re-use and CDP Fallback**: The MCP server dynamically detects running CDP instances at `127.0.0.1:9222`, reusing active browser host daemons without crashing on profile lock files (`SingletonLock`).
4. **Structured Error Handling**: All tools catch exceptions internally and return clean, descriptive error messages instead of terminating the agent session or raising unhandled RPC errors.

---

## CLI Reference

| Command | Arguments | Description |
|---|---|---|
| `ensure-host` (or `start-host`) | `[--headless] [--port PORT]` | Starts persistent Chrome daemon in background if not running. |
| `stop-host` | `[--port PORT]` | Gracefully stops the background Chrome daemon. |
| `open` | `[url] [--detach] [--headless] [--port PORT]` | Opens persistent browser to URL. Auto-spawns background host if called non-interactively. |
| `login` | `[--port PORT]` | Opens visible Chrome to perform initial Google login. |
| `status` | `[--port PORT]` | Displays profile path, CDP readiness, initialization status, disk size, and cookie count. |
| `clean-locks` | None | Removes stale lockfiles if browser process crashed unexpectedly. |

### Global Options
- `--profile PATH`: Override profile directory location.
- `--channel {chrome,msedge}`: Specify browser executable channel (default: `chrome`).
- `--port PORT`: Set Chrome DevTools Protocol remote debugging port (default: `9222`).

---

## Troubleshooting

### Lockfile Errors / SingletonLock Collision
If a previous automation script was forcefully killed, a stale lockfile may prevent new instances:
```bash
playwright-chrome clean-locks
```

### Headless Mode Redirects
Some services enforce stricter anti-bot checks on headless mode. If a site challenges headless automation, launch in non-headless mode or specify realistic viewport sizes and user agents.
