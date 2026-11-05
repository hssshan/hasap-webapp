"""Reference exchange rates from a free, keyless API (open.er-api.com, daily updates).

Shows market CNY/USD and the official TMT/USD so you can see if your person's
3.1 TMT per CNY drifts far from the market. Does NOT change your pricing rate.
"""
from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import Session

from ..settings_store import get_setting, set_setting
from .base import Adapter

URL = "https://open.er-api.com/v6/latest/USD"


class FxAdapter(Adapter):
    name = "fx"
    label = "Reference exchange rates"
    kind = "reference"
    needs = []
    free_note = "Free, no key (open.er-api.com)."
    unlocks = "Compare your person's CNY→TMT rate with the market; USD view in the currency toggle."

    def __init__(self, client: httpx.Client | None = None):
        self.client = client

    def run(self, db: Session, **kwargs: Any) -> dict[str, Any]:
        client = self.client or httpx.Client(timeout=15)
        r = client.get(URL)
        r.raise_for_status()
        rates = r.json().get("rates", {})
        cny, tmt = rates.get("CNY"), rates.get("TMT")
        if not cny:
            raise RuntimeError("no CNY rate in response")
        data = {"cny_per_usd": cny, "tmt_per_usd_official": tmt, "updated": r.json().get("time_last_update_utc")}
        if tmt:
            data["official_tmt_per_cny"] = round(tmt / cny, 4)
        set_setting(db, "fx", data)
        set_setting(db, "pricing", {"usd_cny_rate": round(cny, 4)})
        return {"cny_per_usd": cny, "tmt_per_usd": tmt or 0}


def fx_view(db: Session) -> dict[str, Any]:
    fx = get_setting(db, "fx") or {}
    rate = float(get_setting(db, "pricing")["yuan_to_tmt"])
    out = {"person_tmt_per_cny": rate, **fx}
    if fx.get("official_tmt_per_cny"):
        diff = (rate / fx["official_tmt_per_cny"] - 1) * 100
        out["drift_pct_vs_official"] = round(diff, 1)
        out["note"] = (f"Your rate is {diff:.0f}% {'above' if diff > 0 else 'below'} the official rate "
                       f"({fx['official_tmt_per_cny']:.3f}). The street rate in Turkmenistan is usually far above official, "
                       f"so compare against what other cargo agents charge, not this number alone.")
    return out
