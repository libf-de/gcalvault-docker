#!/usr/bin/env python3

import sys
from src import Gcalvault, GcalvaultError


def main():
    try:
        Gcalvault().run(sys.argv[1:])
        return 0
    except GcalvaultError as e:
        print(f"gcalvault: {e}", file=sys.stderr)
        print("gcalvault: Run 'gcalvault --help' for more information", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
