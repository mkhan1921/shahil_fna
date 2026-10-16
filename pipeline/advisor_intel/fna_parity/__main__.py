"""``python -m advisor_intel.fna_parity <command>``: the FNA engine parity harness (§12.7).

- ``generate``: run the TypeScript engine on N seeded documents (needs Node and the app's node_modules)
- ``check``: compare the Python port with what ``generate`` wrote
- ``fixture``: refresh the committed subset in pipeline/tests/fixtures/fna_parity_subset.jsonl.gz
- ``regenerate``: all three, in that order
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .harness import DEFAULT_N, DEFAULT_OUT, DEFAULT_SEED, FIXTURE, check, generate, write_fixture


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m advisor_intel.fna_parity", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("command", choices=["generate", "check", "fixture", "regenerate"])
    p.add_argument("--n", type=int, default=DEFAULT_N, help=f"documents to generate (default {DEFAULT_N})")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output directory (default {DEFAULT_OUT})")
    p.add_argument("--fixture-count", type=int, default=25, help="documents in the committed subset (default 25)")
    args = p.parse_args(argv)

    if args.command in ("generate", "regenerate"):
        seconds = generate(args.out, n=args.n, seed=args.seed)
        print(f"TypeScript: {args.n} documents written to {args.out} in {seconds:.1f}s")
    if args.command in ("check", "regenerate"):
        summary = check(args.out)
        print(summary.describe())
        if not summary.ok:
            return 1
    if args.command in ("fixture", "regenerate"):
        chosen = write_fixture(args.out, FIXTURE, count=args.fixture_count)
        print(f"wrote {len(chosen)} documents to {FIXTURE}: {chosen}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
