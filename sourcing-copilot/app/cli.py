"""Command line helpers.

    python -m app.cli seed              # load demo data (only if DB is empty)
    python -m app.cli clear-mock        # delete demo data, keep everything you entered
    python -m app.cli run <adapter>     # run one source now: youtube | google_trends | telegram_channels | fx
    python -m app.cli recompute         # recompute trend scores
    python -m app.cli import-json FILE  # import a vidIQ JSON file (same format as the scheduled task)
    python -m app.cli telegram-login    # one-time Telethon login → prints TELEGRAM_SESSION for .env
    python -m app.cli secret            # print a random secret for TREND_IMPORT_SECRET / APP_SECRET_KEY
"""
from __future__ import annotations

import json
import secrets
import sys

from .db import SessionLocal, init_engine


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "secret":
        print(secrets.token_urlsafe(32))
        return 0
    if cmd == "telegram-login":
        from .adapters.telegram_channels import telegram_login
        telegram_login()
        return 0
    init_engine()
    db = SessionLocal()
    try:
        if cmd == "seed":
            from .seed import seed
            print(seed(db))
        elif cmd == "clear-mock":
            from .seed import clear_mock
            print("Deleted:", clear_mock(db))
        elif cmd == "run" and len(argv) > 1:
            from .adapters.registry import REGISTRY
            from . import services
            print(REGISTRY[argv[1]].safe_run(db, force=True))
            print("alerts:", services.recompute_trends(db))
        elif cmd == "recompute":
            from . import services
            print(services.recompute_trends(db))
        elif cmd == "import-json" and len(argv) > 1:
            from .adapters.vidiq_import import import_payload
            with open(argv[1], encoding="utf-8") as f:
                print(import_payload(db, json.load(f)))
        else:
            print(__doc__)
            return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
