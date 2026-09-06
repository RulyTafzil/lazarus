"""CLI entry point for running ned_mcp directly."""

import sys
from .server import main

if __name__ == "__main__":
    sys.exit(main())
