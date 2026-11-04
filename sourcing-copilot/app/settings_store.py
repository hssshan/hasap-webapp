"""Editable business settings (pricing, delivery, thresholds, channels...).

Stored as JSON in the `settings` table; defaults below are used for any key not
yet saved. Edit on the Settings screen or via PUT /api/settings.
"""
from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from .db import Setting

DEFAULTS: dict[str, Any] = {
    "pricing": {
        "yuan_to_tmt": 3.1,          # what my person charges per 1 CNY
        "cargo_tmt_per_kg": 145.0,   # real weight only, no volumetric, no minimum
        "fee_per_item_tmt": 0.0,     # fee per item to my person
        "loss_buffer": 0.05,         # 5% damaged / unsold
        "markup": 2.0,
        "round_to_small": 5,         # round selling price to nearest 5 under 100 TMT
        "round_to_large": 10,        # ... and nearest 10 from 100 TMT up
        "usd_cny_rate": 7.1,         # fallback CNY per USD for the currency toggle
    },
    "delivery": {
        # [min, typical, max] days
        "seller_dispatch_days": [1, 2, 4],
        "china_domestic_days": [2, 3, 5],
        "cargo_transit_days": [12, 15, 20],
    },
    "trends": {
        "alert_threshold": 70,
        "min_independent_signals": 2,
        "single_signal_cap": 35,
        "youtube_queries_tr": ["oyuncak kutu açılımı", "Çin'den aldım", "peluş oyuncak", "hediye fikirleri", "kilo ile oyuncak", "squishy"],
        "youtube_queries_ru": ["распаковка игрушек", "заказала из китая", "мягкая игрушка", "идеи подарков"],
        "google_trends_geos": ["TR", "RU", "TM"],
    },
    "telegram": {
        "channels": [],          # public channel usernames, e.g. ["tm_oyunjaklar", "ashgabat_market"]
        "person_chat_id": None,  # chat id of my person in Turkmenistan
        "digest_weekday": 0,     # 0 = Monday
        "digest_hour_utc": 8,
    },
    "ui": {"currency": "TMT"},
    "ai": {"monthly_budget_usd": 5.0},
}


def get_setting(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    default = copy.deepcopy(DEFAULTS.get(key))
    if row is None:
        return default
    value = row.value
    if isinstance(default, dict) and isinstance(value, dict):
        merged = default
        merged.update(value)
        return merged
    return value


def set_setting(db: Session, key: str, value: Any) -> Any:
    current = get_setting(db, key)
    if isinstance(current, dict) and isinstance(value, dict):
        current.update(value)
        value = current
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = copy.deepcopy(value)
    db.commit()
    return value


def all_settings(db: Session) -> dict[str, Any]:
    return {k: get_setting(db, k) for k in DEFAULTS}
