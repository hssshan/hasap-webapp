"""Pluggable data-source adapters.

Each adapter declares the env vars it needs and implements `run(db)`. Missing
keys never raise: `run` returns {"status": "skipped", ...}. Failures are caught,
recorded in `job_runs` with a retry time, and the app carries on. To add a paid
source later, subclass `Adapter`, implement `run`, and add it to REGISTRY.
"""
from __future__ import annotations

import logging
import traceback
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ..config import env
from ..db import JobRun, utcnow

log = logging.getLogger(__name__)


class Adapter:
    name: str = "base"
    label: str = "Base"
    kind: str = "trend"  # trend | local_market | offer | reference
    needs: list[str] = []          # env vars that must all be set
    optional: list[str] = []
    free_note: str = ""
    unlocks: str = ""
    automatic: bool = True         # runs on a schedule
    retry_minutes: int = 360

    def configured(self) -> bool:
        return all(env(n) for n in self.needs)

    def status(self, db: Session) -> dict[str, Any]:
        job = db.get(JobRun, self.name)
        return {
            "name": self.name, "label": self.label, "kind": self.kind, "configured": self.configured(),
            "needs": self.needs, "automatic": self.automatic, "free_note": self.free_note, "unlocks": self.unlocks,
            "last_run": job.last_run.isoformat() if job and job.last_run else None,
            "last_ok": job.last_ok.isoformat() if job and job.last_ok else None,
            "last_status": job.status if job else "never", "message": job.message if job else None,
            "retry_after": job.retry_after.isoformat() if job and job.retry_after else None,
        }

    def run(self, db: Session, **kwargs: Any) -> dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError

    def safe_run(self, db: Session, *, force: bool = False, **kwargs: Any) -> dict[str, Any]:
        job = db.get(JobRun, self.name) or JobRun(name=self.name)
        db.add(job)
        now = utcnow()
        if not self.configured():
            job.last_run, job.status = now, "skipped"
            job.message = f"Not configured: set {', '.join(n for n in self.needs if not env(n))} in .env"
            db.commit()
            return {"status": "skipped", "message": job.message}
        if job.retry_after and job.retry_after > now and not force:
            return {"status": "waiting", "message": f"Backing off until {job.retry_after:%H:%M} UTC after an error"}
        try:
            result = self.run(db, **kwargs) or {}
            job = db.get(JobRun, self.name) or job
            job.last_run = job.last_ok = utcnow()
            job.status = result.get("status", "ok")
            job.message = result.get("message") or _summ(result)
            job.retry_after = None
            db.commit()
            return {"status": job.status, **result}
        except Exception as e:  # noqa: BLE001 - never crash the scheduler
            db.rollback()
            log.warning("adapter %s failed: %s", self.name, e)
            job = db.get(JobRun, self.name) or JobRun(name=self.name)
            db.add(job)
            job.last_run, job.status = utcnow(), "error"
            job.message = f"{type(e).__name__}: {e}"[:500]
            job.retry_after = utcnow() + timedelta(minutes=self.retry_minutes)
            db.commit()
            log.debug(traceback.format_exc())
            return {"status": "error", "message": job.message}


def _summ(result: dict) -> str:
    parts = [f"{k}={v}" for k, v in result.items() if isinstance(v, (int, float, str)) and k != "status"]
    return ", ".join(parts)[:400] or "ok"


def mark_job(db: Session, name: str, status: str, message: str) -> None:
    job = db.get(JobRun, name) or JobRun(name=name)
    db.add(job)
    job.last_run = utcnow()
    if status == "ok":
        job.last_ok = job.last_run
    job.status, job.message = status, message[:500]
    db.commit()


def since(dt: datetime | None, days: float) -> bool:
    return dt is None or dt < utcnow() - timedelta(days=days)
