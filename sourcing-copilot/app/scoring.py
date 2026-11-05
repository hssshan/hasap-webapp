"""All scores are explainable: each returns a total plus a list of components
with points, max points and a human-readable reason.

* Seller reliability (0-100)
* Offer value score (0-100): landing cost + quality signals + seller reliability
* Trend score (0-100) + lifecycle state (rising / peaking / fading)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


@dataclass
class Component:
    key: str
    label: str
    points: float
    max_points: float
    reason: str
    links: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "points": round(self.points, 1),
                "max": self.max_points, "reason": self.reason, "links": self.links}


def _total(components: list[Component]) -> float:
    return max(0.0, min(100.0, sum(c.points for c in components)))


def colour(score: float) -> str:
    return "green" if score >= 70 else "amber" if score >= 45 else "red"


# ============================================================ seller reliability

EVENT_PENALTY = {"delay": -6, "damage": -10, "wrong_item": -12, "missing": -15, "good": 3}


def seller_score(seller: Any, events: Iterable[Any] = ()) -> dict:
    comps: list[Component] = []
    age = seller.store_age_years
    if age is None:
        comps.append(Component("age", "Store age", 6, 20, "unknown store age (neutral)"))
    else:
        pts = min(20, 4 + age * 3.2)
        comps.append(Component("age", "Store age", pts, 20, f"{age:g} years open" + (" — new store, be careful" if age < 1 else "")))

    sales = seller.total_sales
    if sales is None:
        comps.append(Component("sales", "Total sales", 6, 20, "unknown sales volume (neutral)"))
    else:
        pts = min(20, max(0, math.log10(max(sales, 1)) * 4))  # 100 → 8, 10k → 16, 100k → 20
        comps.append(Component("sales", "Total sales", pts, 20, f"{sales:,} sales"))

    rating = seller.rating
    if rating is None:
        comps.append(Component("rating", "Store rating", 8, 20, "no store rating shown (neutral)"))
    else:
        pts = max(0, min(20, (rating - 4.0) * 20))  # 4.0 → 0, 4.5 → 10, 5.0 → 20
        comps.append(Component("rating", "Store rating", pts, 20, f"{rating:.1f}/5" + (" — below 4.5 is a warning sign" if rating < 4.5 else "")))

    rr = seller.response_rate
    if rr is None:
        comps.append(Component("response", "Response rate", 4, 10, "unknown response rate"))
    else:
        comps.append(Component("response", "Response rate", max(0, min(10, (rr - 70) / 3)), 10, f"{rr:.0f}% responds"))

    dr = seller.dispute_rate
    if dr is None:
        comps.append(Component("disputes", "Refunds / disputes", 8, 15, "no dispute data (neutral)"))
    else:
        comps.append(Component("disputes", "Refunds / disputes", max(0, 15 - dr * 5), 15, f"{dr:.1f}% refunds/disputes"))

    if seller.is_factory is True:
        comps.append(Component("factory", "Factory vs reseller", 15, 15, "factory / source manufacturer"))
    elif seller.is_factory is False:
        comps.append(Component("factory", "Factory vs reseller", 6, 15, "reseller (middleman)"))
    else:
        comps.append(Component("factory", "Factory vs reseller", 7, 15, "unknown if factory"))

    events = list(events)
    if events:
        adj = 0.0
        counts: dict[str, int] = {}
        for e in events:
            adj += EVENT_PENALTY.get(e.kind, 0)
            counts[e.kind] = counts.get(e.kind, 0) + 1
        adj = max(-40, min(10, adj))
        desc = ", ".join(f"{n}× {k.replace('_', ' ')}" for k, n in counts.items())
        comps.append(Component("experience", "My experience", adj, 10, f"my history: {desc}"))
    total = _total(comps)
    return {"score": round(total), "colour": colour(total), "components": [c.as_dict() for c in comps]}


# ============================================================ offer value score

def offer_value_scores(offers: list[Any], landing_by_offer: dict[int, float], seller_scores: dict[int, float]) -> dict[int, dict]:
    """Rank offers for one product. Landing cost is scored relative to the cheapest offer."""
    if not offers:
        return {}
    cheapest = min(landing_by_offer.get(o.id, 1e9) for o in offers) or 1
    out: dict[int, dict] = {}
    for o in offers:
        comps: list[Component] = []
        landing = landing_by_offer.get(o.id)
        if landing:
            ratio = landing / cheapest
            pts = max(0, 35 - (ratio - 1) * 70)  # 0% dearer → 35, 25% dearer → 17.5, 50% → 0
            comps.append(Component("cost", "Landing cost", pts, 35,
                                   "cheapest landing cost" if ratio <= 1.001 else f"{(ratio - 1) * 100:.0f}% dearer than cheapest"))
        # quality: 40 pts
        q = 0.0
        notes = []
        if o.rating is not None:
            q += max(0, min(14, (o.rating - 4.0) * 14))
            notes.append(f"{o.rating:.1f}/5 rating")
        else:
            q += 5
            notes.append("no rating")
        if o.review_count is not None:
            q += min(10, math.log10(max(o.review_count, 1)) * 3.3)
            notes.append(f"{o.review_count:,} reviews")
        else:
            q += 3
        if o.buyer_photos:
            q += min(5, o.buyer_photos / 10)
            notes.append(f"{o.buyer_photos} buyer photos")
        if o.repeat_purchase_rate:
            q += min(5, o.repeat_purchase_rate / 6)
            notes.append(f"{o.repeat_purchase_rate:.0f}% repeat buyers")
        if o.sales_count:
            q += min(6, math.log10(max(o.sales_count, 1)) * 1.5)
            notes.append(f"{o.sales_count:,} sold")
        complaints = o.complaints or []
        serious = [c for c in complaints if any(w in c.lower() for w in ("smell", "shed", "broken", "smaller", "stitch", "fake", "damaged", "wrong"))]
        if complaints:
            q -= min(12, 2 * len(complaints) + 2 * len(serious))
            notes.append(f"{len(complaints)} complaint themes" + (f" ({', '.join(serious[:2])})" if serious else ""))
        comps.append(Component("quality", "Quality signals", max(0, min(40, q)), 40, ", ".join(notes)))
        ss = seller_scores.get(o.seller_id or -1)
        if ss is None:
            comps.append(Component("seller", "Seller reliability", 10, 25, "seller unknown (neutral)"))
        else:
            comps.append(Component("seller", "Seller reliability", ss / 100 * 25, 25, f"seller score {ss:.0f}/100"))
        total = _total(comps)
        out[o.id] = {"score": round(total), "colour": colour(total), "components": [c.as_dict() for c in comps]}
    return out


def why_winner(ranked: list[dict]) -> str:
    """One line on why #1 beats #2. `ranked` items: {"offer_id", "value": {...}, "landing_tmt", "title"}."""
    if not ranked:
        return ""
    if len(ranked) == 1:
        return "Only offer so far: add 1-2 more to compare."
    a, b = ranked[0], ranked[1]
    ca = {c["key"]: c["points"] for c in a["value"]["components"]}
    cb = {c["key"]: c["points"] for c in b["value"]["components"]}
    labels = {"cost": "cheaper landing cost", "quality": "better ratings/reviews", "seller": "more reliable seller"}
    diffs = sorted(((ca.get(k, 0) - cb.get(k, 0), k) for k in labels), reverse=True)
    best_gain, best_key = diffs[0]
    saved = (b.get("landing_tmt") or 0) - (a.get("landing_tmt") or 0)
    reason = labels[best_key]
    extra = ""
    if best_key == "cost" and saved > 0:
        extra = f" (saves {saved:.0f} TMT/unit)"
    elif saved < 0:
        extra = f", worth the extra {-saved:.0f} TMT/unit"
    return f"#1 wins on {reason}{extra}: value {a['value']['score']} vs {b['value']['score']}."


# ============================================================ trend score

LANG_WEIGHT = {"tr": 1.0, "ru": 0.6, "tk": 1.0, "en": 0.3}


def _recency(dt: datetime | None, now: datetime, half_life_days: float = 14) -> float:
    if dt is None:
        return 0.5
    age = max(0.0, (now - dt).total_seconds() / 86400)
    return 0.5 ** (age / half_life_days)


def trend_score(signals: list[Any], *, sales_30d: int = 0, sales_prev_30d: int = 0,
                local_listings: int = 0, snapshots: list[tuple[datetime, float]] | None = None,
                now: datetime | None = None, min_independent: int = 2, single_cap: float = 35) -> dict:
    """signals: TrendSignal-like objects with source, platform, views, outlier_score, value, language,
    creator, published_at/collected_at, url, title."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    social = [s for s in signals if s.source in ("vidiq", "youtube", "paste") and s.classification == "product"]
    search = [s for s in signals if s.source == "google_trends"]
    local = [s for s in signals if s.source == "telegram"]
    comps: list[Component] = []

    def link(s):
        return {"url": s.url, "title": (s.title or "")[:90], "source": s.source, "platform": s.platform, "views": s.views}

    # 1. Social reach (30): log views × language weight × recency
    reach = 0.0
    for s in social:
        w = LANG_WEIGHT.get(s.language or "", 0.5) * _recency(s.published_at or s.collected_at, now)
        reach += w * math.log10(max(s.views or 0, 10))
    reach_pts = min(30, reach * 2.2)
    top_views = sorted(social, key=lambda s: s.views or 0, reverse=True)
    comps.append(Component("reach", "Social reach (TR/RU videos)", reach_pts, 30,
                           f"{len(social)} product videos, {sum(s.views or 0 for s in social):,} views total"
                           if social else "no product videos yet",
                           [link(s) for s in top_views[:8]]))

    # 2. Outlier strength (10)
    outliers = [s.outlier_score for s in social if s.outlier_score]
    o_pts = min(10, math.log2(max(outliers)) * 2) if outliers else 0
    comps.append(Component("outlier", "Outlier strength", max(0, o_pts), 10,
                           f"best video did {max(outliers):.0f}× the creator's usual views" if outliers else "no outlier data"))

    # 3. Independence / breadth (15)
    keys = set()
    for s in social + local:
        keys.add((s.platform or s.source, (s.creator or s.external_id or "").lower()))
    platforms = {s.platform or s.source for s in social + local}
    independent = len(keys)
    b_pts = min(15, max(0, independent - 1) * 3.5 + max(0, len(platforms) - 1) * 2)
    comps.append(Component("breadth", "Independent signals", b_pts, 15,
                           f"{independent} independent creators/channels on {len(platforms)} platform(s): {', '.join(sorted(platforms)) or '—'}"))

    # 4. Search interest (10): Google Trends latest value & slope
    s_pts = 0.0
    reason = "no Google Trends data (free source is unreliable; retries automatically)"
    if search:
        best = max(search, key=lambda s: s.value or 0)
        slope = (best.raw or {}).get("slope", 0) or 0
        s_pts = min(10, (best.value or 0) / 12 + (3 if slope > 0 else 0))
        reason = f"Google Trends {best.raw.get('geo', '')}: interest {best.value:.0f}/100, {'rising' if slope > 0 else 'flat/falling'}"
    comps.append(Component("search", "Search interest", s_pts, 10, reason))

    # 5. Local market (10): Turkmen Telegram channels already selling it = demand proven locally
    n_local = max(local_listings, len(local))
    l_pts = min(10, n_local * 2.5)
    comps.append(Component("local", "Ashgabat market (Telegram)", l_pts, 10,
                           f"{n_local} local listings seen" if n_local else "not seen in local channels yet",
                           [link(s) for s in local[:5]]))

    # 6. Own sales (25) — strongest signal
    if sales_30d:
        own = min(25, 8 + sales_30d * 1.2)
        growth = "growing" if sales_30d > sales_prev_30d else "steady" if sales_30d == sales_prev_30d else "slowing"
        comps.append(Component("sales", "My own sales", own, 25, f"{sales_30d} sold in last 30 days ({growth})"))
    else:
        comps.append(Component("sales", "My own sales", 0, 25, "no sales yet"))

    total = _total(comps)
    capped = False
    if independent < min_independent and not sales_30d:
        if total > single_cap:
            total = single_cap
            capped = True

    state, state_reason = lifecycle(signals, snapshots or [], sales_30d, sales_prev_30d, now)
    return {
        "score": round(total),
        "colour": colour(total),
        "independent_signals": independent,
        "is_trend": independent >= min_independent or sales_30d > 0,
        "capped": capped,
        "cap_reason": f"Only {independent} independent signal(s): one viral video is not a trend (capped at {single_cap:.0f})." if capped else None,
        "state": state,
        "state_reason": state_reason,
        "components": [c.as_dict() for c in comps],
    }


def activity_index(signals: list[Any], start: datetime, end: datetime) -> float:
    total = 0.0
    for s in signals:
        if s.source not in ("vidiq", "youtube", "paste", "telegram") or s.classification == "noise":
            continue
        dt = s.published_at or s.collected_at
        if dt and start <= dt < end:
            total += math.log10(max(s.views or 0, 10))
    return total


def lifecycle(signals: list[Any], snapshots: list[tuple[datetime, float]], sales_30d: int, sales_prev: int,
              now: datetime) -> tuple[str, str]:
    """rising / peaking / fading / unknown — compares recent vs previous activity,
    Google Trends slope and score history."""
    recent = activity_index(signals, now - timedelta(days=14), now + timedelta(days=1))
    prev = activity_index(signals, now - timedelta(days=28), now - timedelta(days=14))
    gt_slopes = [(s.raw or {}).get("slope", 0) or 0 for s in signals if s.source == "google_trends"]
    gt = sum(gt_slopes) / len(gt_slopes) if gt_slopes else 0
    snap_trend = 0.0
    if len(snapshots) >= 3:
        snaps = sorted(snapshots)
        half = len(snaps) // 2
        first = sum(v for _, v in snaps[:half]) / half
        last = sum(v for _, v in snaps[half:]) / (len(snaps) - half)
        snap_trend = last - first

    votes = 0.0
    why = []
    if recent or prev:
        if prev == 0 and recent > 0:
            votes += 1
            why.append("new videos in the last 2 weeks")
        elif recent > prev * 1.3:
            votes += 1
            why.append(f"video activity up {((recent / prev) - 1) * 100:.0f}% vs prior 2 weeks")
        elif recent < prev * 0.7:
            votes -= 1
            why.append(f"video activity down {(1 - recent / prev) * 100:.0f}% vs prior 2 weeks" if prev else "few new videos")
        else:
            why.append("video activity flat")
    if gt:
        votes += 0.7 if gt > 0 else -0.7
        why.append("search interest " + ("rising" if gt > 0 else "falling"))
    if snap_trend:
        if snap_trend > 5:
            votes += 0.5
            why.append("score climbing")
        elif snap_trend < -5:
            votes -= 0.5
            why.append("score dropping")
    if sales_30d or sales_prev:
        if sales_30d > sales_prev * 1.2:
            votes += 0.7
            why.append("my sales growing")
        elif sales_30d < sales_prev * 0.7:
            votes -= 0.7
            why.append("my sales slowing")
    if not why:
        return "unknown", "not enough history yet"
    if votes >= 0.9:
        return "rising", "; ".join(why)
    if votes <= -0.9:
        return "fading", "; ".join(why)
    return "peaking", "; ".join(why) + " — momentum levelling off"
