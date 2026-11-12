"""Command-line helpers."""

from __future__ import annotations

import sys
from pathlib import Path

from maestro.pack import PackError, load_pack


def validate_main(argv: list[str] | None = None) -> int:
    """Validate maestro packs: `maestro-validate maestros/rafi maestros/eddy-arnold`.

    With no arguments, validates every folder under maestros/.
    """
    args = sys.argv[1:] if argv is None else argv
    paths = [Path(a) for a in args] or sorted(p for p in Path("maestros").iterdir() if p.is_dir())
    failed = 0
    for path in paths:
        try:
            pack = load_pack(path)
        except PackError as exc:
            failed += 1
            print(f"FAIL {exc}")
        else:
            print(f"ok   {pack.slug} ({pack.data['status']}): unit={pack.unit}, container={pack.container}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(validate_main())
