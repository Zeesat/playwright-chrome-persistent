import argparse
import sys
import os
import sqlite3
from pathlib import Path
from .core import (
    launch_persistent_browser,
    get_profile_dir,
    is_profile_initialized,
    clean_locks,
    is_cdp_active,
    connect_cdp,
    DEFAULT_CDP_PORT,
    DEFAULT_CDP_HOST,
)


def cmd_login(args: argparse.Namespace) -> int:
    """Handle the interactive login command."""
    profile_path = get_profile_dir(args.profile)
    print("=" * 65)
    print("  PLAYWRIGHT PERSISTENT CHROME - INTERACTIVE LOGIN")
    print(f"  Target Profile: {profile_path}")
    print("=" * 65)

    if hasattr(args, "port") and not (1 <= args.port <= 65535):
        print(f"[-] Error: Port must be between 1 and 65535, got {args.port}", file=sys.stderr)
        return 1

    port = getattr(args, "port", DEFAULT_CDP_PORT)
    if is_cdp_active(port=port):
        print(f"[!] Warning: Active Chrome instance detected on CDP port {port}.")
        print("[!] Note: Launching another instance on the same profile may cause lock conflicts.")

    print("[+] Launching Chrome persistent context in visible mode...")

    p, context, page = launch_persistent_browser(
        headless=False,
        user_data_dir=profile_path,
        channel=args.channel,
        cdp_port=port,
    )

    login_url = "https://accounts.google.com/"
    print(f"[+] Navigating to: {login_url}")
    page.goto(login_url)

    print("\n" + "-" * 65)
    print("Sign in to your Google account in the opened Chrome window.")
    print("Once login is completed, return here and press [ENTER] to save the session.")
    print("-" * 65)

    try:
        input("\n[Press ENTER after finishing Google login]: ")
    except (EOFError, KeyboardInterrupt):
        pass

    current_title = page.title()
    current_url = page.url
    print(f"\n[+] Active Page Title: {current_title}")
    print(f"[+] Active Page URL:   {current_url}")

    context.close()
    p.stop()

    print("\n[+] Success! Session data and credentials saved permanently in:")
    print(f"    {profile_path}")
    print("[+] Subsequent automation runs using this profile will stay logged in.")
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    """Open persistent browser to a specified URL, reusing active CDP session if running."""
    profile_path = get_profile_dir(args.profile)
    url = args.url or "https://myaccount.google.com/"

    if hasattr(args, "port") and not (1 <= args.port <= 65535):
        print(f"[-] Error: Port must be between 1 and 65535, got {args.port}", file=sys.stderr)
        return 1

    port = getattr(args, "port", DEFAULT_CDP_PORT)
    if is_cdp_active(port=port):
        print(f"[+] Active Chrome instance detected on CDP port {port}.")
        p, browser, context, page = connect_cdp(port=port)
        current_url = page.url.strip().rstrip("/")
        if current_url in ("about:blank", ""):
            target_page = page
        else:
            target_page = context.new_page()

        target_page.goto(url)
        print(f"[+] Reconnected over CDP and opened: {url}")
        try:
            browser.close()
        except Exception:
            pass
        p.stop()
        return 0

    print(f"[+] Opening {url} with profile: {profile_path} (headless={args.headless})")
    p, context, page = launch_persistent_browser(
        headless=args.headless,
        user_data_dir=profile_path,
        channel=args.channel,
        cdp_port=port,
    )

    page.goto(url)
    print(f"[+] Page loaded: {page.title()} ({page.url})")

    if not args.headless:
        print("[+] Press [ENTER] in terminal to close the browser session...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass

    context.close()
    p.stop()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Inspect and report profile initialization, CDP endpoint, and cookie state."""
    profile_path = Path(get_profile_dir(args.profile))
    print(f"Profile Directory: {profile_path}")
    print(f"Exists:            {profile_path.exists()}")

    if hasattr(args, "port") and not (1 <= args.port <= 65535):
        print(f"[-] Error: Port must be between 1 and 65535, got {args.port}", file=sys.stderr)
        return 1

    port = getattr(args, "port", DEFAULT_CDP_PORT)
    cdp_active = is_cdp_active(port=port)
    if cdp_active:
        print(f"CDP Status (127.0.0.1:{port}): Active (Ready for agent attach)")
    else:
        print(f"CDP Status (127.0.0.1:{port}): Inactive")

    if not profile_path.exists():
        print("Status:            Not initialized. Run 'playwright-chrome login' first.")
        return 1

    initialized = is_profile_initialized(str(profile_path))
    print(f"Initialized:       {initialized}")

    # Calculate directory size
    total_size = sum(f.stat().st_size for f in profile_path.rglob("*") if f.is_file())
    size_mb = total_size / (1024 * 1024)
    print(f"Disk Usage:        {size_mb:.2f} MB")

    # Check cookies database
    cookies_db = profile_path / "Default" / "Network" / "Cookies"
    if not cookies_db.exists():
        cookies_db = profile_path / "Network" / "Cookies"

    if cookies_db.exists():
        try:
            conn = sqlite3.connect(f"file:{cookies_db}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM cookies")
            total_cookies = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM cookies WHERE host_key LIKE '%google%'")
            google_cookies = cur.fetchone()[0]
            print(f"Total Cookies:     {total_cookies}")
            print(f"Google Cookies:    {google_cookies}")
            conn.close()
        except Exception as err:
            print(f"Cookies Status:    Database locked or unreadable ({err})")
    else:
        print("Cookies DB:        Not found (will be created on first browse)")

    return 0


def cmd_clean_locks(args: argparse.Namespace) -> int:
    """Clean stale lockfiles from the profile directory."""
    profile_path = get_profile_dir(args.profile)
    removed = clean_locks(profile_path)
    print(f"[+] Removed {removed} lockfile(s) from {profile_path}.")
    return 0


def main():
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--profile",
        "-p",
        help="Path to custom profile directory (defaults to .chrome_profile in cwd or PLAYWRIGHT_CHROME_PROFILE)",
        default=argparse.SUPPRESS,
    )
    common_parser.add_argument(
        "--channel",
        "-c",
        help="Browser channel to use (default: chrome)",
        default=argparse.SUPPRESS,
    )
    common_parser.add_argument(
        "--port",
        type=int,
        default=argparse.SUPPRESS,
        help=f"CDP remote debugging port (default: {DEFAULT_CDP_PORT})",
    )

    parser = argparse.ArgumentParser(
        prog="playwright-chrome",
        description="Persistent Google Chrome profile manager for Playwright automation without session reset.",
    )
    parser.add_argument(
        "--profile",
        "-p",
        help="Path to custom profile directory (defaults to .chrome_profile in cwd or PLAYWRIGHT_CHROME_PROFILE)",
        default=None,
    )
    parser.add_argument(
        "--channel",
        "-c",
        help="Browser channel to use (default: chrome)",
        default="chrome",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_CDP_PORT,
        help="CDP remote debugging port (default: 9222)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # login command
    login_parser = subparsers.add_parser(
        "login",
        parents=[common_parser],
        help="Open visible Chrome to login Google account once",
    )
    login_parser.set_defaults(func=cmd_login)

    # open command
    open_parser = subparsers.add_parser(
        "open",
        parents=[common_parser],
        help="Open persistent browser session to a URL",
    )
    open_parser.add_argument("url", nargs="?", default="https://myaccount.google.com/", help="URL to open")
    open_parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    open_parser.set_defaults(func=cmd_open)

    # status command
    status_parser = subparsers.add_parser(
        "status",
        parents=[common_parser],
        help="Display profile status, disk usage, and cookies",
    )
    status_parser.set_defaults(func=cmd_status)

    # clean-locks command
    locks_parser = subparsers.add_parser(
        "clean-locks",
        parents=[common_parser],
        help="Remove stale lock files if browser crashed",
    )
    locks_parser.set_defaults(func=cmd_clean_locks)

    args = parser.parse_args()
    if hasattr(args, "port") and not (1 <= args.port <= 65535):
        parser.error(f"argument --port: Port must be between 1 and 65535, got {args.port}")

    if not args.command:
        # Default action when run with no subcommands is login helper
        return cmd_login(args)

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
