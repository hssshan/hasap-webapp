"""Pricing engine — the exact rules from the brief, all parameters editable.

    goods_tmt   = (item_price_cny + china_domestic_shipping_cny) * yuan_to_tmt
    cargo_tmt   = weight_kg * cargo_tmt_per_kg
    landing_tmt = (goods_tmt + cargo_tmt + fee_per_item_tmt) * (1 + loss_buffer)
    sell_tmt    = landing_tmt * markup   (then rounded to a sensible local price)
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Any, Iterable

# Typical shipping weights (kg, including poly bag) used when a listing has no weight.
WEIGHT_ESTIMATES: list[tuple[str, float, str]] = [
    (r"kilo|by the kilo|kg\b", 1.0, "sold by weight: priced per 1 kg"),
    (r"keychain|keyring|anahtarl|брелок|charm", 0.04, "keychains are ~30-50 g"),
    (r"squish|stress|антистресс|fidget", 0.06, "small squishy toys are ~40-80 g"),
    (r"mini.*camera|digital camera|kamera|камера", 0.25, "mini cameras with box ~200-300 g"),
    (r"crochet|örgü|amigurumi|вязан", 0.12, "crocheted plush ~80-150 g"),
    (r"big|large|giant|büyük|больш|100 ?cm|80 ?cm", 0.9, "large plush ~0.6-1.2 kg"),
    (r"plush|peluş|мягк|oýnawaç|doll|bebek|кукл", 0.25, "medium plush 25-35 cm ~200-300 g"),
    (r"blind box|surprise|sürpriz|сюрприз", 0.15, "blind boxes ~120-180 g"),
]
DEFAULT_WEIGHT = (0.3, "generic small item ~300 g")


def estimate_weight(name: str, category: str = "") -> tuple[float, str]:
    text = f"{name} {category}".lower()
    for pattern, kg, why in WEIGHT_ESTIMATES:
        if re.search(pattern, text):
            return kg, why
    return DEFAULT_WEIGHT


def round_local_price(value: float, small_step: int = 5, large_step: int = 10) -> float:
    """Round to a price people actually charge in Ashgabat: nearest 5 under 100, nearest 10 above."""
    if value <= 0:
        return 0.0
    step = small_step if value < 100 else large_step
    rounded = round(value / step) * step
    return float(max(step, rounded))


def unit_price_for_qty(base_price: float, tiers: Iterable[dict] | None, qty: int) -> float:
    """Pick the cheapest applicable price tier for a quantity."""
    price = base_price
    for tier in tiers or []:
        try:
            min_qty = int(tier.get("min_qty", 0))
            tier_price = float(tier.get("price_cny"))
        except (TypeError, ValueError):
            continue
        if qty >= min_qty and tier_price > 0:
            price = min(price, tier_price) if price else tier_price
    return price


@dataclass
class PriceResult:
    item_price_cny: float
    domestic_shipping_cny: float
    weight_kg: float
    weight_is_estimate: bool
    weight_note: str
    goods_tmt: float
    cargo_tmt: float
    fee_tmt: float
    buffer_tmt: float
    landing_tmt: float
    raw_sell_tmt: float
    sell_tmt: float
    profit_tmt: float
    margin_pct: float
    cargo_pct_of_cost: float
    rules: dict = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, float):
                d[k] = round(v, 2)
        return d


def calculate(
    item_price_cny: float,
    domestic_shipping_cny: float = 0.0,
    weight_kg: float | None = None,
    rules: dict | None = None,
    *,
    name: str = "",
    category: str = "",
    markup: float | None = None,
) -> PriceResult:
    from .settings_store import DEFAULTS

    r = dict(DEFAULTS["pricing"])
    r.update(rules or {})
    yuan_to_tmt = float(r["yuan_to_tmt"])
    cargo_per_kg = float(r["cargo_tmt_per_kg"])
    fee = float(r["fee_per_item_tmt"])
    loss = float(r["loss_buffer"])
    mk = float(markup if markup is not None else r["markup"])

    estimate = weight_kg is None or weight_kg <= 0
    note = ""
    if estimate:
        weight_kg, note = estimate_weight(name, category)
    assert weight_kg is not None

    goods = (float(item_price_cny) + float(domestic_shipping_cny or 0)) * yuan_to_tmt
    cargo = weight_kg * cargo_per_kg
    pre_buffer = goods + cargo + fee
    landing = pre_buffer * (1 + loss)
    raw_sell = landing * mk
    sell = round_local_price(raw_sell, int(r.get("round_to_small", 5)), int(r.get("round_to_large", 10)))
    profit = sell - landing
    margin = (profit / sell * 100) if sell else 0.0
    cargo_pct = (cargo / pre_buffer * 100) if pre_buffer else 0.0
    return PriceResult(
        item_price_cny=float(item_price_cny),
        domestic_shipping_cny=float(domestic_shipping_cny or 0),
        weight_kg=weight_kg,
        weight_is_estimate=estimate,
        weight_note=note,
        goods_tmt=goods,
        cargo_tmt=cargo,
        fee_tmt=fee,
        buffer_tmt=landing - pre_buffer,
        landing_tmt=landing,
        raw_sell_tmt=raw_sell,
        sell_tmt=sell,
        profit_tmt=profit,
        margin_pct=margin,
        cargo_pct_of_cost=cargo_pct,
        rules={"yuan_to_tmt": yuan_to_tmt, "cargo_tmt_per_kg": cargo_per_kg, "fee_per_item_tmt": fee,
               "loss_buffer": loss, "markup": mk},
    )


def price_offer(offer: Any, rules: dict, qty: int = 1) -> PriceResult:
    unit = unit_price_for_qty(offer.price_cny or 0, offer.price_tiers, qty)
    product = getattr(offer, "product", None)
    weight = offer.weight_kg or (product.est_weight_kg if product is not None else None)
    estimate_flag = not offer.weight_kg
    res = calculate(unit, offer.domestic_shipping_cny or 0, weight, rules,
                    name=(product.name if product else "") + " " + (offer.title_en or ""),
                    category=product.category if product else "")
    if estimate_flag and not res.weight_is_estimate:
        res.weight_is_estimate = True
        res.weight_note = "weight from product estimate (listing shows none)"
    return res


def local_price_check(sell_tmt: float, local_prices: list[float]) -> dict[str, Any]:
    """Compare my suggested price with prices seen in Turkmen Telegram channels / entered by me."""
    prices = sorted(p for p in local_prices if p and p > 0)
    if not prices:
        return {"status": "no_data", "message": "No local prices yet: add channels in Settings or enter one manually."}
    med = median(prices)
    diff_pct = (sell_tmt - med) / med * 100
    if sell_tmt > prices[-1]:
        status, msg = "above_market", f"Above every local price seen ({prices[0]:.0f}–{prices[-1]:.0f} TMT). Expect slow sales."
    elif diff_pct > 10:
        status, msg = "above_median", f"{diff_pct:.0f}% above the local median of {med:.0f} TMT."
    elif diff_pct < -25:
        status, msg = "well_below", f"{-diff_pct:.0f}% below the local median of {med:.0f} TMT: you could charge more."
    else:
        status, msg = "in_range", f"In line with the local market (median {med:.0f} TMT)."
    return {"status": status, "message": msg, "median": med, "min": prices[0], "max": prices[-1],
            "count": len(prices), "diff_pct": round(diff_pct, 1)}


def convert(tmt: float, currency: str, rules: dict) -> float:
    if currency == "CNY":
        return tmt / float(rules["yuan_to_tmt"])
    if currency == "USD":
        return tmt / float(rules["yuan_to_tmt"]) / float(rules.get("usd_cny_rate") or 7.1)
    return tmt


def ceil_to(value: float, step: float) -> float:
    return math.ceil(value / step) * step
