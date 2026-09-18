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
)


def cmd_login(args: argparse.Namespace) -> int:
    """Handle the interactive login command."""
    profile_path = get_profile_dir(args.profile)
    print("=" * 65)
    print("  PLAYWRIGHT PERSISTENT CHROME - INTERACTIVE LOGIN")
    print(f"  Target Profile: {profile_path}")
    print("=" * 65)
    print("[+] Launching Chrome persistent context in visible mode...")

    p, context, page = launch_persistent_browser(
        headless=False,
        user_data_dir=profile_path,
        channel=args.channel,
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
    """Open persistent browser to a specified URL."""
    profile_path = get_profile_dir(args.profile)
    url = args.url or "https://myaccount.google.com/"

    print(f"[+] Opening {url} with profile: {profile_path} (headless={args.headless})")
    p, context, page = launch_persistent_browser(
        headless=args.headless,
        user_data_dir=profile_path,
        channel=args.channel,
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
    """Inspect and report profile initialization and cookie state."""
    profile_path = Path(get_profile_dir(args.profile))
    print(f"Profile Directory: {profile_path}")
    print(f"Exists:            {profile_path.exists()}")

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

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # login command
    login_parser = subparsers.add_parser("login", help="Open visible Chrome to login Google account once")
    login_parser.set_defaults(func=cmd_login)

    # open command
    open_parser = subparsers.add_parser("open", help="Open persistent browser session to a URL")
    open_parser.add_argument("url", nargs="?", default="https://myaccount.google.com/", help="URL to open")
    open_parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    open_parser.set_defaults(func=cmd_open)

    # status command
    status_parser = subparsers.add_parser("status", help="Display profile status, disk usage, and cookies")
    status_parser.set_defaults(func=cmd_status)

    # clean-locks command
    locks_parser = subparsers.add_parser("clean-locks", help="Remove stale lock files if browser crashed")
    locks_parser.set_defaults(func=cmd_clean_locks)

    args = parser.parse_args()
    if not args.command:
        # Default action when run with no subcommands is login helper
        return cmd_login(args)

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
