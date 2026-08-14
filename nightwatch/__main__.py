"""python3 -m nightwatch — the same entry point the `nightwatch` shim calls."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
