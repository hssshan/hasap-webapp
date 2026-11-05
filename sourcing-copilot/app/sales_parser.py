"""Parse quick sales messages, e.g. from the Telegram bot or the quick-entry box.

Accepted forms (English / Russian / Turkmen verbs, case-insensitive):
    sold 2 hamster 250          -> 2 units of "hamster" at 250 TMT each
    sold hamster 250            -> 1 unit
    sold 3x squishy @ 40        -> 3 units at 40 each
    sold 2 camera 500 total     -> 2 units, 500 TMT total (250 each)
    продал 2 хомяк 250 / satdym 2 hamster 250
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .product_filter import norm

VERBS = r"(?:sold|sell|sale|продал[аи]?|продано|продажа|satdym|satyldy|satdyk|satyş)"
_PATTERN = re.compile(
    rf"^\s*/?{VERBS}\s+(?:(?P<qty>\d+)\s*(?:x|×|pcs|шт|sany)?\s+)?(?P<name>.+?)\s+@?\s*(?P<price>\d+(?:[.,]\d+)?)"
    r"\s*(?:tmt|m|manat|ман|манат)?\s*(?P<total>total|jemi|всего|итого|for all)?\s*(?:each|per|за шт|her biri)?\s*$",
    re.IGNORECASE,
)


@dataclass
class ParsedSale:
    qty: int
    name: str
    unit_price: float
    total: float


def parse_sale(text: str) -> ParsedSale | None:
    m = _PATTERN.match(text.strip())
    if not m:
        return None
    qty = int(m.group("qty") or 1)
    if qty <= 0 or qty > 10000:
        return None
    price = float(m.group("price").replace(",", "."))
    name = m.group("name").strip(" -:,")
    name = re.sub(r"\s+(x|×|pcs|шт)$", "", name, flags=re.I)
    if not name or price <= 0:
        return None
    if m.group("total"):
        return ParsedSale(qty, name, round(price / qty, 2), price)
    return ParsedSale(qty, name, price, round(price * qty, 2))


def match_product(name: str, products: list) -> tuple[object | None, list]:
    """Find the product a short name refers to. Returns (product or None, candidates)."""
    q = norm(name)
    q_words = [w for w in re.findall(r"\w+", q) if len(w) > 1]
    scored = []
    for p in products:
        hay = " ".join([norm(p.name)] + [norm(k) for k in (p.keywords or [])])
        if q == norm(p.name):
            return p, [p]
        hits = sum(1 for w in q_words if w in hay or any(h.startswith(w[:5]) for h in hay.split() if len(w) >= 5))
        if hits:
            scored.append((hits / max(len(q_words), 1), p))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        return None, []
    best = [p for s, p in scored if s == scored[0][0]]
    if len(best) == 1 and scored[0][0] >= 0.5:
        return best[0], best
    return None, [p for _, p in scored[:5]]
