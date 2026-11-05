"""Claude API layer — optional, cached, budget-capped.

* Without ANTHROPIC_API_KEY every function returns a useful non-AI fallback
  (rules, templates, empty forms), so the app keeps working.
* Results are cached in the `ai_cache` table by a hash of the input.
* Small model (Haiku) for classification / short text; vision model (Sonnet)
  only for screenshots + review summaries.
* A monthly USD budget (Settings → AI, default $5) stops calls when exceeded.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import product_filter
from .config import env, model_small, model_vision
from .db import AICache, UsageLog
from .settings_store import get_setting

log = logging.getLogger(__name__)

# $ per 1M tokens (input, output). Used for budget tracking only.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}


def available() -> bool:
    return bool(env("ANTHROPIC_API_KEY"))


def _client():
    import anthropic  # lazy import

    return anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"), max_retries=2, timeout=60)


def month_spend(db: Session) -> float:
    start = datetime.now(timezone.utc).replace(tzinfo=None).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = db.scalar(select(func.sum(UsageLog.units)).where(UsageLog.provider == "claude", UsageLog.created_at >= start))
    return float(total or 0)


def budget_ok(db: Session) -> bool:
    budget = float(get_setting(db, "ai").get("monthly_budget_usd", 5.0))
    return month_spend(db) < budget


def status(db: Session) -> dict[str, Any]:
    return {
        "connected": available(),
        "month_spend_usd": round(month_spend(db), 4),
        "budget_usd": float(get_setting(db, "ai").get("monthly_budget_usd", 5.0)),
        "model_small": model_small(),
        "model_vision": model_vision(),
    }


def _cache_key(kind: str, payload: Any) -> str:
    raw = kind + "|" + json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def cache_get(db: Session, kind: str, payload: Any) -> Any | None:
    row = db.get(AICache, _cache_key(kind, payload))
    return row.value if row else None


def cache_put(db: Session, kind: str, payload: Any, value: Any) -> None:
    key = _cache_key(kind, payload)
    row = db.get(AICache, key)
    if row:
        row.value = value
    else:
        db.add(AICache(key=key, kind=kind, value=value))
    db.commit()


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in a model reply (tolerates code fences)."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start = text.find(open_c)
        end = text.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON in model reply")


def _call(db: Session, model: str, system: str, content: list | str, max_tokens: int = 2000,
          effort: str = "low") -> str | None:
    """One Messages API call with usage logging. Returns text or None on any failure."""
    if not available() or not budget_ok(db):
        return None
    try:
        import anthropic

        client = _client()
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            output_config={"effort": effort},
            messages=[{"role": "user", "content": content}],
        )
        usage = resp.usage
        pin, pout = PRICES.get(model, (2.0, 10.0))
        cost = (usage.input_tokens * pin + usage.output_tokens * pout) / 1_000_000
        db.add(UsageLog(provider="claude", units=cost, detail=f"{model} in={usage.input_tokens} out={usage.output_tokens}"))
        db.commit()
        if resp.stop_reason == "refusal":
            return None
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    except anthropic.APIStatusError as e:  # 4xx/5xx
        log.warning("Claude API error %s: %s", e.status_code, e.message)
    except anthropic.APIConnectionError as e:
        log.warning("Claude API connection error: %s", e)
    except Exception as e:  # never crash a request because AI failed
        log.warning("Claude call failed: %s", e)
    return None


# ---------------------------------------------------------------- classification

CLASSIFY_SYSTEM = """You filter social media posts for a toy/gift reseller (China -> Turkmenistan).
For each item decide if a BUYABLE PHYSICAL PRODUCT is the subject (unboxing, haul, store find, gift idea,
"nereden aldın", "link bio'da", "Çin'den sipariş", kilo toy sales, claw machines) -> "product".
Lip syncs, dances, songs, skits, food jokes, relationship memes, outfits shown while dancing -> "noise".
For products give a short clean generic English product name (e.g. "crocheted hamster plush",
"mini digital camera", "plush toys sold by the kilo"), a category (plush, toys, electronics, accessories,
stationery, beauty, home, other), and counterfeit=true if it is a branded/licensed character
(Labubu, Disney, Sanrio, Pokemon, anime, etc.).
Reply ONLY with a JSON array: [{"i":0,"label":"product|noise","product":"...","category":"...",
"counterfeit":false,"reason":"<=12 words"}]"""


def classify_items(db: Session, texts: list[str]) -> list[product_filter.Classification]:
    """Rules first; ambiguous items go to Claude in one batched call (cached)."""
    results = [product_filter.classify(t) for t in texts]
    todo = [i for i, r in enumerate(results) if r.label == "unknown"]
    if not todo or not available():
        return results
    uncached = []
    for i in todo:
        hit = cache_get(db, "classify", texts[i])
        if hit:
            results[i] = _merge_ai(results[i], hit)
        else:
            uncached.append(i)
    for chunk_start in range(0, len(uncached), 25):
        chunk = uncached[chunk_start:chunk_start + 25]
        payload = "\n".join(f"{n}. {texts[i][:400]}" for n, i in enumerate(chunk))
        reply = _call(db, model_small(), CLASSIFY_SYSTEM, payload, max_tokens=2000)
        if not reply:
            continue
        try:
            parsed = extract_json(reply)
        except ValueError:
            continue
        for item in parsed if isinstance(parsed, list) else []:
            try:
                idx = chunk[int(item["i"])]
            except (KeyError, ValueError, IndexError, TypeError):
                continue
            cache_put(db, "classify", texts[idx], item)
            results[idx] = _merge_ai(results[idx], item)
    return results


def _merge_ai(rule: product_filter.Classification, item: dict) -> product_filter.Classification:
    label = item.get("label") if item.get("label") in ("product", "noise") else rule.label
    name = (item.get("product") or rule.product_name or "").strip() or None
    canon = product_filter.canonical_product(name or "") if name else None
    return product_filter.Classification(
        label=label,
        product_name=(canon[0] if canon else name) if label == "product" else None,
        category=item.get("category") or (canon[1] if canon else rule.category),
        emoji=canon[2] if canon else (rule.emoji or ""),
        reason="AI: " + str(item.get("reason", ""))[:120],
        counterfeit=bool(item.get("counterfeit")) or rule.counterfeit,
        counterfeit_reason=rule.counterfeit_reason or ("AI flagged as branded/licensed: customs may seize counterfeits."
                                                      if item.get("counterfeit") else None),
        confidence=0.8,
    )


# ---------------------------------------------------------------- search terms

FALLBACK_TERMS = {
    "plush toys sold by the kilo": {"zh": ["毛绒玩具 按斤卖", "娃娃机 公仔 批发", "毛绒公仔 散货 称斤"], "tr": "kilo ile peluş oyuncak", "ru": "мягкие игрушки на вес", "tk": "kilo bilen ýumşak oýnawaç"},
    "crocheted hamster plush": {"zh": ["钩针 仓鼠 玩偶", "手工编织 仓鼠 公仔", "毛线 仓鼠 挂件"], "tr": "örgü hamster peluş", "ru": "вязаный хомяк игрушка", "tk": "örme hamster oýnawaç"},
    "mini digital camera": {"zh": ["迷你 数码相机 学生", "复古 小相机 CCD", "儿童 数码相机 便宜"], "tr": "mini dijital kamera", "ru": "мини цифровая камера", "tk": "mini sanly kamera"},
    "squishy toys": {"zh": ["捏捏乐 解压玩具", "慢回弹 捏捏 批发", "解压 软软 玩具"], "tr": "squishy oyuncak", "ru": "сквиш антистресс", "tk": "squishy oýnawaç"},
    "doll style keychains": {"zh": ["娃娃 钥匙扣 挂件", "毛绒 公仔 钥匙链", "包包 挂件 玩偶"], "tr": "bebek anahtarlık", "ru": "брелок кукла", "tk": "gurjak açar halkasy"},
    "capybara plush": {"zh": ["水豚 毛绒玩具", "卡皮巴拉 公仔"], "tr": "kapibara peluş", "ru": "капибара игрушка", "tk": "kapibara oýnawaç"},
    "labubu style plush": {"zh": ["毛绒 公仔 挂件 原创", "怪兽 毛绒 玩偶"], "tr": "labubu peluş", "ru": "лабубу игрушка", "tk": "labubu oýnawaç"},
    "blind box figures": {"zh": ["盲盒 手办 批发", "迷你 公仔 盲盒"], "tr": "sürpriz kutu figür", "ru": "блайнд бокс фигурки", "tk": "syrly guty figura"},
}


def search_terms(db: Session, product_name: str, category: str = "") -> dict:
    key = {"name": product_name, "category": category}
    if product_name in FALLBACK_TERMS:
        return FALLBACK_TERMS[product_name]
    cached = cache_get(db, "terms", key)
    if cached:
        return cached
    system = ("You write marketplace search terms. Reply ONLY JSON: {\"zh\": [3 short Simplified Chinese search "
              "phrases for Taobao/1688, cheapest-wholesale friendly], \"tr\": \"Turkish name\", \"ru\": \"Russian name\", "
              "\"tk\": \"Turkmen name\"}")
    reply = _call(db, model_small(), system, f"Product: {product_name} (category: {category})", max_tokens=400)
    if reply:
        try:
            data = extract_json(reply)
            if isinstance(data, dict) and data.get("zh"):
                cache_put(db, "terms", key, data)
                return data
        except ValueError:
            pass
    return {"zh": [product_name], "tr": product_name, "ru": product_name, "tk": product_name, "fallback": True}


# ---------------------------------------------------------------- vision: offer screenshots

OFFER_SYSTEM = """You read screenshots of Taobao / 1688 / Tmall listing pages and review pages (Chinese UI).
Extract what is visible; use null when not shown. Translate text to English. Prices in CNY (¥).
Reply ONLY with JSON:
{"title": "original title", "title_en": "English title", "price_cny": number|null,
 "price_tiers": [{"min_qty": int, "price_cny": number}], "domestic_shipping_cny": number|null,
 "sales_count": int|null, "rating": number|null (0-5), "review_count": int|null, "buyer_photos": int|null,
 "repeat_purchase_rate": number|null (%), "weight_kg": number|null, "size": string|null, "variants": string|null,
 "store_name": string|null, "store_age_years": number|null, "store_rating": number|null (0-5),
 "store_total_sales": int|null, "response_rate": number|null (%), "dispute_rate": number|null (%),
 "is_factory": bool|null (1688 "源头工厂"/"实力商家" -> true),
 "praises": ["top praise in English", ...max 4], "complaints": ["top complaint in English", ...max 4]}
For praises/complaints look for: shedding, smell, smaller than listed, poor stitching, colour different,
broken, slow shipping, good quality, soft, as pictured."""

_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}


def extract_offer(db: Session, image_paths: list[Path], url: str = "") -> dict[str, Any]:
    if not image_paths:
        return {"ai": False, "message": "No screenshots: fill the form manually."}
    if not available():
        return {"ai": False, "message": "Claude API key not set: fill the form manually (Settings → Setup to enable AI)."}
    digest = hashlib.sha256(b"".join(p.read_bytes() for p in image_paths)).hexdigest()
    cached = cache_get(db, "offer", {"digest": digest})
    if cached:
        return {"ai": True, "cached": True, "fields": cached}
    content: list[dict] = []
    for p in image_paths[:5]:
        content.append({"type": "image", "source": {"type": "base64", "media_type": _MEDIA.get(p.suffix.lower(), "image/jpeg"),
                                                     "data": base64.standard_b64encode(p.read_bytes()).decode()}})
    content.append({"type": "text", "text": f"Listing URL: {url or 'unknown'}. Extract the fields."})
    reply = _call(db, model_vision(), OFFER_SYSTEM, content, max_tokens=2500, effort="low")
    if not reply:
        return {"ai": False, "message": "AI could not read the screenshots (budget, network or API error). Fill manually."}
    try:
        data = extract_json(reply)
    except ValueError:
        return {"ai": False, "message": "AI reply was not readable. Fill manually."}
    cache_put(db, "offer", {"digest": digest}, data)
    return {"ai": True, "fields": data}


# ---------------------------------------------------------------- explanations & captions

def explain_recommendations(db: Session, cards: list[dict]) -> dict[int, str]:
    """One-line explanation per recommendation. Template always; AI rewrite (batched, cached) if available."""
    out = {c["product_id"]: template_explanation(c) for c in cards}
    if not available() or not cards:
        return out
    key = [(c["product_id"], round(c["trend_score"]), c.get("trend_state"), round(c.get("profit_tmt") or 0, -1)) for c in cards]
    cached = cache_get(db, "explain", key)
    if cached:
        out.update({int(k): v for k, v in cached.items()})
        return out
    system = ("You advise a solo reseller (China -> Ashgabat). For each product write ONE plain sentence (max 22 words) "
              "saying why to order it now or what to watch out for. Reply ONLY JSON {\"<product_id>\": \"sentence\"}.")
    facts = json.dumps([{k: c.get(k) for k in ("product_id", "name", "trend_score", "trend_state", "signals",
                                               "landing_tmt", "sell_tmt", "profit_tmt", "seller_score",
                                               "delivery", "local_check", "counterfeit_risk")} for c in cards],
                       ensure_ascii=False, default=str)
    reply = _call(db, model_small(), system, facts, max_tokens=1500)
    if reply:
        try:
            data = extract_json(reply)
            if isinstance(data, dict):
                cache_put(db, "explain", key, data)
                out.update({int(k): str(v) for k, v in data.items() if str(k).isdigit()})
        except ValueError:
            pass
    return out


def template_explanation(c: dict) -> str:
    parts = []
    state = c.get("trend_state")
    if state == "rising":
        parts.append("Rising trend")
    elif state == "peaking":
        parts.append("At its peak: order small, it may cool before cargo lands")
    elif state == "fading":
        parts.append("Fading: only reorder if your own sales are strong")
    else:
        parts.append("Early signal")
    if c.get("signals"):
        parts.append(f"{c['signals']} independent signals")
    if c.get("profit_tmt"):
        parts.append(f"~{c['profit_tmt']:.0f} TMT profit/unit")
    if c.get("own_sales_30d"):
        parts.append(f"you sold {c['own_sales_30d']} in 30 days")
    if c.get("counterfeit_risk"):
        parts.append("branded: customs risk")
    lc = c.get("local_check") or {}
    if lc.get("status") in ("above_market", "above_median"):
        parts.append("priced above local market")
    elif lc.get("status") == "well_below" and lc.get("median"):
        parts.append(f"local shops charge ~{lc['median']:.0f} TMT: you can price higher")
    return "; ".join(parts) + "."


def captions(db: Session, product_name: str, sell_tmt: float, features: list[str] | None = None) -> dict:
    """Customer-facing captions in Turkmen and Russian (Instagram / Telegram post)."""
    key = {"p": product_name, "price": sell_tmt, "f": features or []}
    cached = cache_get(db, "captions", key)
    if cached:
        return cached
    fallback = template_captions(product_name, sell_tmt)
    if not available():
        return fallback
    system = ("You write short, warm social-media sales posts for a small shop in Ashgabat, Turkmenistan. "
              "Write natural Turkmen (Latin alphabet) and Russian. Include price in TMT, a call to DM/order, 3-5 hashtags. "
              "No brand names. No emojis. Reply ONLY JSON {\"tk\": {\"title\": \"\", \"caption\": \"\"}, \"ru\": {\"title\": \"\", \"caption\": \"\"}}")
    reply = _call(db, model_small(), system, json.dumps(key, ensure_ascii=False), max_tokens=900)
    if reply:
        try:
            data = extract_json(reply)
            if isinstance(data, dict) and "tk" in data and "ru" in data:
                cache_put(db, "captions", key, data)
                return data
        except ValueError:
            pass
    return fallback


def template_captions(product_name: str, sell_tmt: float) -> dict:
    terms = FALLBACK_TERMS.get(product_name, {})
    tk_name = terms.get("tk", product_name)
    ru_name = terms.get("ru", product_name)
    price = f"{sell_tmt:.0f} TMT"
    return {
        "tk": {"title": tk_name.capitalize(),
               "caption": f"Täze geldi: {tk_name}! Bahasy: {price}. Sargyt üçin DM ýazyň ýa-da jaň ediň "
                          f"Aşgabat boýunça eltip berýäris \n#Aşgabat #sowgat #oýnawaç #täzelik", "template": True},
        "ru": {"title": ru_name.capitalize(),
               "caption": f"Новинка: {ru_name}! Цена: {price}. Для заказа пишите в директ "
                          f"Доставка по Ашхабаду \n#Ашхабад #подарки #игрушки #новинка", "template": True},
    }


def clean_paste(db: Session, text: str) -> list[dict]:
    """Turn a messy paste (hashtags, product names, links from TikTok Creative Center / Pinterest Trends)
    into items. Rules always; AI adds product names for vague lines."""
    items = []
    for line in re.split(r"[\n\r]+", text):
        line = line.strip(" \t•-*,;")
        if not line:
            continue
        url_m = re.search(r"https?://\S+", line)
        url = url_m.group(0) if url_m else None
        platform = "paste"
        if url:
            for p in ("tiktok", "pinterest", "instagram", "youtube", "youtu.be", "t.me"):
                if p in url:
                    platform = {"youtu.be": "youtube", "t.me": "telegram"}.get(p, p)
        tags = re.findall(r"#(\w+)", line)
        label = re.sub(r"https?://\S+", "", line).strip() or (url or "")
        views_m = re.search(r"(\d+(?:[.,]\d+)?)\s*([kmb]|bin|mn|млн|тыс)\b", line.lower())
        views = 0
        if views_m:
            num = float(views_m.group(1).replace(",", "."))
            mult = {"k": 1e3, "bin": 1e3, "тыс": 1e3, "m": 1e6, "mn": 1e6, "млн": 1e6, "b": 1e9}[views_m.group(2)]
            views = int(num * mult)
        items.append({"text": label, "url": url, "platform": platform, "hashtags": tags, "views": views})
    return items
