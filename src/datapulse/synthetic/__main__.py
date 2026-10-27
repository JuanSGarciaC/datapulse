"""CLI: ``python -m datapulse.synthetic [--seed N] [--out DIR] [--check]``."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from datapulse.synthetic.generator import GeneratorConfig, check, write


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Genera datos sintéticos reproducibles.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/samples"))
    parser.add_argument(
        "--check", action="store_true", help="no escribe; falla si los archivos no coinciden"
    )
    args = parser.parse_args(argv)
    cfg = GeneratorConfig(seed=args.seed)

    if args.check:
        problems = check(cfg, args.out)
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        if problems:
            print("Regenera con: python -m datapulse.synthetic", file=sys.stderr)
            return 1
        print(f"OK: {args.out} coincide con seed={cfg.seed}")
        return 0

    meta = write(cfg, args.out)
    kinds = Counter(d["kind"] for d in meta["defects"])
    print(f"Generados {len(meta['files'])} archivos en {args.out} (seed={cfg.seed})")
    for kind, count in sorted(kinds.items()):
        print(f"  defecto {kind:<26} {count}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
