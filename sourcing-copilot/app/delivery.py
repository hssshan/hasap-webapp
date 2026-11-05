"""Delivery time estimate: seller dispatch + China domestic transit + cargo to Ashgabat.

Starts from editable defaults; once ≥2 shipments have real ordered→arrived dates
the range is learned from history (blended with defaults until there are 5+).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import quantiles
from typing import Any


def estimate(defaults: dict, history_days: list[float] | None = None, seller_delay_days: float = 0) -> dict[str, Any]:
    d = [defaults["seller_dispatch_days"], defaults["china_domestic_days"], defaults["cargo_transit_days"]]
    lo = sum(x[0] for x in d)
    typ = sum(x[1] for x in d)
    hi = sum(x[2] for x in d)
    basis = "defaults (edit in Settings)"
    history = [h for h in (history_days or []) if h and h > 0]
    if len(history) >= 2:
        hs = sorted(history)
        if len(hs) >= 4:
            q = quantiles(hs, n=10)
            h_lo, h_typ, h_hi = q[1], q[4], q[8]
        else:
            h_lo, h_typ, h_hi = hs[0], sum(hs) / len(hs), hs[-1]
        w = min(1.0, len(hs) / 5)
        lo = lo * (1 - w) + h_lo * w
        typ = typ * (1 - w) + h_typ * w
        hi = hi * (1 - w) + h_hi * w
        basis = f"learned from {len(hs)} shipment(s)" + ("" if w >= 1 else " + defaults")
    if seller_delay_days:
        lo += seller_delay_days / 2
        typ += seller_delay_days
        hi += seller_delay_days * 1.5
        basis += f"; +{seller_delay_days:.0f}d for this seller's past delays"
    lo, typ, hi = round(lo), round(typ), round(hi)
    return {"min_days": lo, "typical_days": typ, "max_days": hi, "label": f"{lo} to {hi} days", "basis": basis,
            "parts": {"seller_dispatch": defaults["seller_dispatch_days"],
                      "china_domestic": defaults["china_domestic_days"],
                      "cargo_transit": defaults["cargo_transit_days"]}}


def arrival_window(est: dict, start: datetime | None = None) -> dict[str, str]:
    start = start or datetime.now(timezone.utc).replace(tzinfo=None)
    return {"earliest": (start + timedelta(days=est["min_days"])).date().isoformat(),
            "typical": (start + timedelta(days=est["typical_days"])).date().isoformat(),
            "latest": (start + timedelta(days=est["max_days"])).date().isoformat()}
