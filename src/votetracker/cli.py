"""Command line entry point.

    python -m votetracker run                 # fetch, build, publish
    python -m votetracker run --store local   # write to ./build instead of R2
    python -m votetracker run --no-fetch      # rebuild files from saved data only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from votetracker.config import Config
from votetracker.http import HttpClient
from votetracker.pipeline import Pipeline
from votetracker.store import LocalStore, R2Store


def _store(args, cfg: Config):
    choice = args.store
    if choice == "auto":
        choice = "r2" if cfg.r2_ready() else "local"
    if choice == "r2":
        if not cfg.r2_ready():
            sys.exit("R2 settings missing: set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET")
        return R2Store(cfg.r2_account_id, cfg.r2_access_key_id, cfg.r2_secret_access_key, cfg.r2_bucket)
    return LocalStore(args.out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="votetracker", description="Vote Tracker data pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="fetch new records and publish updated files")
    run.add_argument("--store", choices=("auto", "local", "r2"), default="auto")
    run.add_argument("--out", default="build", help="folder for --store local (default: build)")
    run.add_argument("--no-fetch", action="store_true", help="skip downloading; rebuild from saved data")
    run.add_argument("--no-bills", action="store_true", help="skip Congress.gov bill lookups")
    run.add_argument("--congress", type=int, help="Congress number (default: current)")
    run.add_argument("--max-new", type=int, help="limit new votes per session (for testing)")
    run.add_argument("--full-refresh", action="store_true", help="re-download every vote this Congress")
    run.add_argument("--dry-run", action="store_true", help="do everything except write files")
    run.add_argument("--no-states", action="store_true", help="skip state legislatures")
    run.add_argument("--states", help="only these states, e.g. OH,PA")
    run.add_argument("--no-alerts", action="store_true", help="don't send follow alerts")
    run.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = Config()
    if args.command == "run":
        store = _store(args, cfg)
        http = HttpClient(cfg.user_agent)
        report = Pipeline(cfg, store, http).run(
            fetch=not args.no_fetch,
            bills=not args.no_bills,
            congress=args.congress,
            max_new=args.max_new,
            full_refresh=args.full_refresh,
            dry_run=args.dry_run,
            states=not args.no_states,
            only_states={c.strip().upper() for c in args.states.split(",")} if args.states else None,
            send_alerts=not args.no_alerts,
        )
        print(json.dumps(report, indent=2))
    return 0
