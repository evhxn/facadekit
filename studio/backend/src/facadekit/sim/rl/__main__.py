"""``python -m facadekit.sim.rl <command>``: scan | evaluate (see docs/09-rl-simulation.md)."""

from __future__ import annotations

import argparse
from pathlib import Path

from facadekit.sim.rl.outcomes import GRID_ARMS, PRODUCT_ARMS, build_table, specs_for


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    sc = sub.add_parser("scan", help="fill the outcome table for one split")
    sc.add_argument("--split", required=True)
    sc.add_argument("--n", type=int, required=True)
    sc.add_argument("--realisations", type=int, default=2)
    sc.add_argument("--arms", choices=("product", "grid"), default="product")
    sc.add_argument("--cache", type=Path, required=True)
    sc.add_argument("--workers", type=int, default=None)
    ev = sub.add_parser("evaluate", help="compare policies on the cached tables")
    ev.add_argument("--cache", type=Path, required=True)
    ev.add_argument("--out", type=Path, default=None)
    ev.add_argument("--episodes", type=int, default=4000)
    args = ap.parse_args()
    if args.command == "scan":
        arms = PRODUCT_ARMS if args.arms == "product" else GRID_ARMS
        path = args.cache / f"{args.split}-{args.arms}.jsonl"
        build_table(specs_for(args.split, args.n), arms, args.realisations, path, args.workers)
    else:
        from facadekit.sim.rl.evaluate import run

        run(args.cache, args.out, args.episodes)


if __name__ == "__main__":
    main()
