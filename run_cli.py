import sys
import os

# Ensure local src/ is on Python module search path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from playwright_chrome.cli import main

if __name__ == "__main__":
    sys.exit(main() or 0)
