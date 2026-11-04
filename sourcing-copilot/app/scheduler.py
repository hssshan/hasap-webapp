"""Background refresh jobs (APScheduler, in-process). Every job is wrapped so a
failing free source only logs an error and retries later."""
from __future__ import annotations

import logging

from .adapters.base import mark_job
from .adapters.registry import REGISTRY
from .bot import handlers
from .bot.runner import RUNNER
from .config import env_bool
from .db import SessionLocal
from . import services
from .settings_store import get_setting

log = logging.getLogger(__name__)
_scheduler = None


def run_adapter(name: str) -> dict:
    db = SessionLocal()
    try:
        res = REGISTRY[name].safe_run(db)
        if res.get("status") == "ok" and name in ("youtube", "telegram_channels", "google_trends"):
            recompute_and_alert(db)
        return res
    finally:
        db.close()


def recompute_and_alert(db=None) -> list[dict]:
    own = db is None
    db = db or SessionLocal()
    try:
        alerts = services.recompute_trends(db)
        mark_job(db, "recompute_trends", "ok", f"{len(alerts)} alerts")
        if alerts and RUNNER.active:
            handlers.send_alerts(db, RUNNER.api, alerts)
        return alerts
    finally:
        if own:
            db.close()


def weekly_digest() -> int:
    db = SessionLocal()
    try:
        if not RUNNER.active:
            mark_job(db, "weekly_digest", "skipped", "Telegram bot not connected")
            return 0
        n = handlers.send_digest(db, RUNNER.api)
        mark_job(db, "weekly_digest", "ok", f"sent to {n} chat(s)")
        return n
    finally:
        db.close()


def start() -> None:
    global _scheduler
    if not env_bool("SCHEDULER_ENABLED", True) or _scheduler is not None:
        return
    from apscheduler.schedulers.background import BackgroundScheduler

    db = SessionLocal()
    try:
        tg = get_setting(db, "telegram")
    finally:
        db.close()
    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    s = BackgroundScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})
    s.add_job(run_adapter, "cron", args=["youtube"], hour=5, minute=10, id="youtube")
    s.add_job(run_adapter, "cron", args=["google_trends"], hour=3, minute=40, id="google_trends")
    s.add_job(run_adapter, "interval", args=["telegram_channels"], hours=6, id="telegram_channels")
    s.add_job(run_adapter, "cron", args=["fx"], hour=6, minute=5, id="fx")
    s.add_job(recompute_and_alert, "interval", hours=3, id="recompute")
    s.add_job(weekly_digest, "cron", day_of_week=days[int(tg.get("digest_weekday", 0)) % 7],
              hour=int(tg.get("digest_hour_utc", 8)), minute=3, id="digest")
    s.start()
    _scheduler = s
    log.info("scheduler started")


def shutdown() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
