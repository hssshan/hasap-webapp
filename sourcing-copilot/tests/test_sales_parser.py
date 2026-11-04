from types import SimpleNamespace as NS

import pytest

from app.sales_parser import match_product, parse_sale


@pytest.mark.parametrize("text,qty,name,unit,total", [
    ("sold 2 hamster 250", 2, "hamster", 250, 500),
    ("Sold hamster 250", 1, "hamster", 250, 250),
    ("sold 3x squishy @ 40", 3, "squishy", 40, 120),
    ("sold 2 camera 500 total", 2, "camera", 250, 500),
    ("sold 2 mini camera 380 tmt", 2, "mini camera", 380, 760),
    ("/sold 1 kilo plush 600", 1, "kilo plush", 600, 600),
    ("продал 2 хомяк 250", 2, "хомяк", 250, 500),
    ("satdym 4 hamster 130 manat", 4, "hamster", 130, 520),
    ("sold 2 hamster 12.5", 2, "hamster", 12.5, 25),
])
def test_parse(text, qty, name, unit, total):
    p = parse_sale(text)
    assert p is not None, text
    assert (p.qty, p.name, p.unit_price, p.total) == (qty, name, unit, total)


@pytest.mark.parametrize("text", ["hello", "sold", "sold 2 hamster", "sold 0 hamster 250", "bought 2 hamster 250", ""])
def test_rejects(text):
    assert parse_sale(text) is None


PRODUCTS = [NS(id=1, name="crocheted hamster plush", keywords=["örgü hamster peluş", "вязаный хомяк игрушка"]),
            NS(id=2, name="mini digital camera", keywords=["mini dijital kamera", "мини цифровая камера"]),
            NS(id=3, name="hamster plush", keywords=[])]


def test_match_product_by_keyword_and_language():
    p, _ = match_product("camera", PRODUCTS)
    assert p.id == 2
    p, _ = match_product("хомяк", PRODUCTS)
    assert p.id == 1
    p, _ = match_product("crocheted hamster", PRODUCTS)
    assert p.id == 1


def test_ambiguous_returns_candidates():
    p, cands = match_product("hamster", PRODUCTS)
    assert p is None and {c.id for c in cands} == {1, 3}
    p, cands = match_product("unicorn", PRODUCTS)
    assert p is None and cands == []
