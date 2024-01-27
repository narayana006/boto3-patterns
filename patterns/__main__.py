"""``python -m patterns`` lists the demos; ``python -m patterns NAME`` runs one; ``all`` runs every demo."""

import importlib
import sys

from . import PATTERNS


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("Available patterns (run with: python -m patterns NAME, or 'all'):")
        for name in PATTERNS:
            summary = (importlib.import_module(f"patterns.{name}").__doc__ or "").strip().splitlines()[0]
            print(f"  {name:<15} {summary}")
        return 0
    names = PATTERNS if argv == ["all"] else argv
    unknown = [name for name in names if name not in PATTERNS]
    if unknown:
        print(f"unknown pattern: {', '.join(unknown)}", file=sys.stderr)
        return 2
    for name in names:
        print(f"== {name} ==")
        importlib.import_module(f"patterns.{name}").demo()
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
