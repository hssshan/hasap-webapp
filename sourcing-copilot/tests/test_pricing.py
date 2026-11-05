import pytest

from app import pricing

RULES = {"yuan_to_tmt": 3.1, "cargo_tmt_per_kg": 145, "fee_per_item_tmt": 0, "loss_buffer": 0.05, "markup": 2.0}


def test_exact_formula_from_brief():
    r = pricing.calculate(14.5, 1.0, 0.11, RULES)
    goods = (14.5 + 1.0) * 3.1
    cargo = 0.11 * 145
    landing = (goods + cargo + 0) * 1.05
    assert r.goods_tmt == pytest.approx(goods)
    assert r.cargo_tmt == pytest.approx(cargo)
    assert r.landing_tmt == pytest.approx(landing)
    assert r.raw_sell_tmt == pytest.approx(landing * 2)
    assert r.sell_tmt == 130  # 134.4 → nearest 10 (≥100)
    assert r.profit_tmt == pytest.approx(130 - landing)
    assert r.margin_pct == pytest.approx((130 - landing) / 130 * 100)
    assert r.cargo_pct_of_cost == pytest.approx(cargo / (goods + cargo) * 100)
    assert not r.weight_is_estimate


def test_fee_and_buffer_and_markup_are_editable():
    r = pricing.calculate(10, 0, 1, {**RULES, "fee_per_item_tmt": 5, "loss_buffer": 0.1, "markup": 3})
    landing = (31 + 145 + 5) * 1.1
    assert r.landing_tmt == pytest.approx(landing)
    assert r.raw_sell_tmt == pytest.approx(landing * 3)


@pytest.mark.parametrize("value,expected", [(37.4, 35), (38, 40), (97, 95), (99.9, 100), (134, 130), (136, 140), (2, 5), (0, 0)])
def test_rounding_to_local_prices(value, expected):
    assert pricing.round_local_price(value) == expected


def test_missing_weight_is_estimated_and_flagged():
    r = pricing.calculate(5, 0, None, RULES, name="squishy toy")
    assert r.weight_is_estimate
    assert r.weight_kg == 0.06
    assert "squish" in r.weight_note
    r2 = pricing.calculate(5, 0, 0, RULES, name="something unknown")
    assert r2.weight_is_estimate and r2.weight_kg == 0.3


def test_price_tiers():
    tiers = [{"min_qty": 10, "price_cny": 13}, {"min_qty": 50, "price_cny": 11.5}]
    assert pricing.unit_price_for_qty(14.5, tiers, 1) == 14.5
    assert pricing.unit_price_for_qty(14.5, tiers, 10) == 13
    assert pricing.unit_price_for_qty(14.5, tiers, 80) == 11.5
    assert pricing.unit_price_for_qty(14.5, [{"min_qty": "x"}], 80) == 14.5


def test_local_price_check():
    assert pricing.local_price_check(100, [])["status"] == "no_data"
    assert pricing.local_price_check(200, [120, 150, 160])["status"] == "above_market"
    assert pricing.local_price_check(170, [120, 150, 180])["status"] == "above_median"
    assert pricing.local_price_check(150, [120, 150, 160])["status"] == "in_range"
    low = pricing.local_price_check(90, [120, 150, 160])
    assert low["status"] == "well_below" and low["median"] == 150


def test_currency_conversion():
    rules = {**RULES, "usd_cny_rate": 7.0}
    assert pricing.convert(310, "CNY", rules) == pytest.approx(100)
    assert pricing.convert(310, "USD", rules) == pytest.approx(100 / 7)
    assert pricing.convert(310, "TMT", rules) == 310
