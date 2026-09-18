"""
Persistent Chrome Host Daemon.
Runs a persistent Chrome browser instance in the background exposing CDP on 127.0.0.1:9222.
"""

import os
import sys
import time
import json
import signal
import argparse
from pathlib import Path
from .core import (
    launch_persistent_browser,
    get_profile_dir,
    clean_locks,
    DEFAULT_CDP_PORT,
    DEFAULT_CDP_HOST,
)


def get_host_meta_file(profile_path: str) -> Path:
    return Path(profile_path) / ".cdp_host.json"


def run_host(
    port: int = DEFAULT_CDP_PORT,
    host: str = DEFAULT_CDP_HOST,
    profile: str = None,
    channel: str = "chrome",
    headless: bool = False,
):
    profile_path = get_profile_dir(profile)
    meta_file = get_host_meta_file(profile_path)

    # Clean any stale locks before startup
    clean_locks(profile_path)

    print(f"[HOST] Starting persistent Chrome on {host}:{port} (profile: {profile_path}, headless={headless})")
    p, context, page = launch_persistent_browser(
        headless=headless,
        user_data_dir=profile_path,
        channel=channel,
        cdp_port=port,
        cdp_host=host,
    )

    meta_data = {
        "pid": os.getpid(),
        "port": port,
        "host": host,
        "profile": profile_path,
        "started_at": time.time(),
    }
    try:
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta_data, f)
    except Exception as err:
        print(f"[HOST] Warning: could not write meta file: {err}", file=sys.stderr)

    print(f"[HOST] Chrome host is active and listening on http://{host}:{port}")

    running = True

    def handle_stop(signum, frame):
        nonlocal running
        print(f"\n[HOST] Stop signal received ({signum}). Shutting down gracefully...")
        running = False

    try:
        signal.signal(signal.SIGINT, handle_stop)
        signal.signal(signal.SIGTERM, handle_stop)
    except (ValueError, AttributeError):
        pass

    stop_flag_file = Path(profile_path) / ".stop_host"

    try:
        while running:
            if stop_flag_file.exists():
                print("[HOST] Stop flag file detected. Terminating host...")
                try:
                    stop_flag_file.unlink()
                except OSError:
                    pass
                break
            time.sleep(0.5)
    finally:
        print("[HOST] Closing context and stopping Playwright...")
        try:
            context.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass
        if meta_file.exists():
            try:
                meta_file.unlink()
            except OSError:
                pass
        clean_locks(profile_path)
        print("[HOST] Host shutdown complete.")


def main():
    parser = argparse.ArgumentParser(description="Persistent Chrome CDP Host Daemon")
    parser.add_argument("--port", type=int, default=DEFAULT_CDP_PORT, help="CDP port")
    parser.add_argument("--host", default=DEFAULT_CDP_HOST, help="CDP host")
    parser.add_argument("--profile", "-p", default=None, help="Profile path")
    parser.add_argument("--channel", "-c", default="chrome", help="Browser channel")
    parser.add_argument("--headless", action="store_true", help="Headless mode")

    args = parser.parse_args()
    run_host(
        port=args.port,
        host=args.host,
        profile=args.profile,
        channel=args.channel,
        headless=args.headless,
    )


if __name__ == "__main__":
    main()
