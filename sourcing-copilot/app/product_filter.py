"""Product / noise filter.

Most viral Turkish content is noise (lip syncs, dances, food jokes, skits).
We keep only items where a *buyable physical product* is the subject.

Two layers:
1. Rule-based classifier (always available, free, deterministic, tested).
2. Claude (small model) for items the rules can't decide, when ANTHROPIC_API_KEY
   is set. See `ai.classify_items`. The rule layer runs first so AI is only paid
   for ambiguous items.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Strong signals that a product is the subject (Turkish, Russian, Turkmen, English)
PRODUCT_INTENT = [
    r"kutu aç", r"unbox", r"haul", r"alışveriş", r"nereden ald", r"nerden ald", r"link bio", r"linki bio",
    r"bio'?da", r"bunu almal", r"almalısın", r"çin'?den", r"cinden", r"sipariş", r"temu", r"aliexpress",
    r"trendyol", r"hediye", r"gift", r"store find", r"mağaza", r"magaza", r"pazar", r"bazaar", r"kilo ile",
    r"kiloyla", r"kilo(su|yla)? ", r"tartı", r"weigh", r"claw machine", r"pençe makin", r"vinç", r"oyuncak",
    r"peluş", r"pelus", r"squish", r"anahtarlık", r"kamera", r"fiyat", r"ucuz", r"indirim", r"ürün", r"urun",
    r"распаков", r"покупк", r"заказал", r"из китая", r"с алиэкспресс", r"вайлдберриз", r"wildberries", r"ozon",
    r"подар", r"игрушк", r"брелок", r"купил", r"обзор", r"находк", r"цена",
    r"satyn al", r"bahasy", r"oýnawaç", r"sowgat", r"zakaz",
    r"review", r"must have", r"tiktok made me buy", r"amazon find", r"plush", r"toy", r"keychain", r"camera",
]

NOISE = [
    r"lip ?sync", r"dudak senk", r"dans", r"dance", r"танец", r"танц", r"skeç", r"skit", r"şaka", r"prank",
    r"komik", r"funny", r"meme", r"caps", r"sevgili", r"aşk", r"relationship", r"boyfriend", r"girlfriend",
    r"şarkı", r"song", r"песня", r"cover", r"remix", r"trend sound", r"akım", r"challenge", r"iskender",
    r"yemek", r"tarif", r"recipe", r"mukbang", r"food", r"еда", r"рецепт", r"vlog", r"günlük", r"makyaj",
    r"makeup", r"outfit", r"kombin", r"edit", r"pov", r"duet", r"düet", r"reaction", r"tepki",
]

# Words that, when they are the main focus, still mean noise even if a product appears in the background
NOISE_STRONG = [r"lip ?sync", r"dudak senk", r"dans ederken", r"dancing", r"iskender", r"şarkı söylerken", r"skeç"]

# Canonical product dictionary: (regex, canonical English name, category, emoji)
CANONICAL = [
    (r"labubu", "labubu style plush", "plush", ""),
    (r"(kilo|kg|tart[ıi]|weigh|by the kilo|на вес|кило).{0,40}(pelu[şs]|oyuncak|plush|toy|игрушк)|(pelu[şs]|oyuncak|plush|toy|игрушк).{0,40}(kilo|kg|tart[ıi]|weigh|на вес)",
     "plush toys sold by the kilo", "plush", ""),
    (r"(örgü|orgu|crochet|amigurumi|вязан).{0,40}(hamster|хомяк)|(hamster|хомяк).{0,40}(örgü|orgu|crochet|amigurumi|вязан)",
     "crocheted hamster plush", "plush", ""),
    (r"(mini|küçük|kucuk|cheap|ucuz|retro|мини).{0,30}(dijital |digital )?(kamera|camera|камер|fotoğraf makines)",
     "mini digital camera", "electronics", ""),
    (r"squish|sukuşi|skuşi|антистресс|stres (oyuncağ|topu)|сквиш", "squishy toys", "toys", ""),
    (r"(bebek|doll|кукл|labubu tarz|baby).{0,30}(anahtarl|keychain|брелок|charm)|(anahtarl|keychain|брелок).{0,30}(bebek|doll|кукл)",
     "doll style keychains", "accessories", ""),
    (r"claw machine|pençe|vinç makin|кран.?машин|хватайк", "claw machine plush prizes", "plush", ""),
    (r"blind box|sürpriz kutu|сюрприз бокс|kör kutu", "blind box figures", "toys", ""),
    (r"hamster", "hamster plush", "plush", ""),
    (r"capybara|kapibara|капибар", "capybara plush", "plush", ""),
    (r"pelu[şs]|plush|мягк(ая|ие) игрушк|ýumşak oýnawaç", "plush toys", "plush", ""),
]

# Branded / licensed: customs in Turkmenistan can seize counterfeits
BRANDS = [
    "labubu", "pop mart", "popmart", "disney", "sanrio", "hello kitty", "kuromi", "my melody", "cinnamoroll",
    "pokemon", "pokémon", "pikachu", "marvel", "spider-man", "spiderman", "frozen", "elsa", "mickey", "minnie",
    "stitch", "barbie", "lol surprise", "squishmallow", "jellycat", "sonic", "mario", "nintendo", "naruto",
    "one piece", "demon slayer", "anime", "genshin", "chiikawa", "bt21", "bts", "harry potter", "minecraft",
    "roblox", "huggy wuggy", "paw patrol", "peppa", "cocomelon", "lego", "apple", "airpods", "nike", "adidas",
]


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    return text.replace("i̇", "i")


@dataclass
class Classification:
    label: str  # product | noise | unknown
    product_name: str | None
    category: str | None
    emoji: str | None
    reason: str
    counterfeit: bool
    counterfeit_reason: str | None
    confidence: float

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def detect_brand(text: str) -> str | None:
    t = norm(text)
    for brand in BRANDS:
        if re.search(r"(?<![a-z])" + re.escape(brand) + r"(?![a-z])", t):
            return brand
    return None


def canonical_product(text: str) -> tuple[str, str, str] | None:
    t = norm(text)
    for pattern, name, category, emoji in CANONICAL:
        if re.search(pattern, t):
            return name, category, emoji
    return None


def classify(text: str) -> Classification:
    t = norm(text)
    intent_hits = [p for p in PRODUCT_INTENT if re.search(p, t)]
    noise_hits = [p for p in NOISE if re.search(p, t)]
    strong_noise = [p for p in NOISE_STRONG if re.search(p, t)]
    canon = canonical_product(t)
    brand = detect_brand(t)
    cf_reason = (f"Mentions '{brand}': branded/licensed goods are often counterfeit and can be seized by customs."
                 if brand else None)

    def strip(p: str) -> str:
        return p.replace("\\", "").replace("?", "").replace("(su|yla) ", "")

    if strong_noise and not (canon and len(intent_hits) >= 2):
        return Classification("noise", None, None, None,
                              f"entertainment content ({', '.join(strip(p) for p in strong_noise[:2])})",
                              bool(brand), cf_reason, 0.9)
    score = len(intent_hits) * 1.0 + (2.0 if canon else 0) - len(noise_hits) * 1.2
    if canon and score >= 1.5:
        return Classification("product", canon[0], canon[1], canon[2],
                              f"product is the subject ({', '.join(strip(p) for p in intent_hits[:3]) or canon[0]})",
                              bool(brand), cf_reason, min(0.95, 0.6 + 0.1 * score))
    if score >= 2 and not canon:
        return Classification("unknown", None, None, None,
                              "looks product-related but no product recognised: needs AI or manual review",
                              bool(brand), cf_reason, 0.5)
    if noise_hits and score < 1:
        return Classification("noise", None, None, None,
                              f"no buyable product ({', '.join(strip(p) for p in noise_hits[:2])})",
                              bool(brand), cf_reason, 0.8)
    if score <= 0:
        return Classification("noise", None, None, None, "no product signals", bool(brand), cf_reason, 0.6)
    return Classification("unknown", canon[0] if canon else None, canon[1] if canon else None,
                          canon[2] if canon else None, "weak product signals", bool(brand), cf_reason, 0.4)


_STOP = {"the", "a", "of", "and", "for", "toys", "toy", "plush", "style", "mini", "sold", "by", "with"}


def name_tokens(name: str) -> set[str]:
    words = re.findall(r"[\w']+", norm(name))
    out = set()
    for w in words:
        w = w[:-1] if w.endswith("s") and len(w) > 3 else w
        if w not in _STOP:
            out.add(w)
    return out


def same_product(a: str, b: str) -> bool:
    """Group the same product across videos/platforms (token Jaccard on meaningful words)."""
    if norm(a) == norm(b):
        return True
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    smaller = min(len(ta), len(tb))
    return inter / len(ta | tb) >= 0.5 or (smaller >= 2 and inter == smaller)
