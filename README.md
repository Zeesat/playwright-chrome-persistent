# Playwright Persistent Chrome Profile Manager

A robust, self-contained automation profile manager and CLI utility for Playwright on Windows, macOS, and Linux. It maintains persistent Google account authentication across test and automation runs without session invalidation or cookie resets.

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

## CLI Reference

| Command | Arguments | Description |
|---|---|---|
| `login` | None | Opens visible Chrome to perform initial Google login. |
| `open` | `[url]` `[--headless]` | Opens persistent browser to specified URL (default: Google Account). |
| `status` | None | Displays profile path, initialization status, disk size, and cookie count. |
| `clean-locks` | None | Removes stale lockfiles if browser process crashed unexpectedly. |

### Global Options
- `--profile PATH`: Override profile directory location.
- `--channel {chrome,msedge}`: Specify browser executable channel (default: `chrome`).

---

## Troubleshooting

### Lockfile Errors / SingletonLock Collision
If a previous automation script was forcefully killed, a stale lockfile may prevent new instances:
```bash
playwright-chrome clean-locks
```

### Headless Mode Redirects
Some services enforce stricter anti-bot checks on headless mode. If a site challenges headless automation, launch in non-headless mode or specify realistic viewport sizes and user agents.
