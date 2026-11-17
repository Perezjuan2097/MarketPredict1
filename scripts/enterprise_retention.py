"""Apply plan retention to terminal jobs, reports and resolved alerts for one tenant."""
import argparse
import json

from app.db import get_engine
from app.repositories.sql import SQLRepository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-organization")
    args = parser.parse_args()
    if args.apply and args.confirm_organization != args.organization:
        parser.error("Para aplicar, confirma el slug exacto con --confirm-organization.")
    engine = get_engine()
    try:
        counts = SQLRepository(engine).commercial_purge_operations(args.organization, dry_run=not args.apply)
        print(json.dumps({"dry_run": not args.apply, "counts": counts}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
