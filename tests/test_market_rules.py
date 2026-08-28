from __future__ import annotations

from decimal import Decimal

from xrypto.market_rules import (
    MIN_ORDER_KRW,
    align_price,
    fee,
    is_orderable,
    round_trip_cost,
    tick_size,
)


def test_tick_size_by_price_band():
    assert tick_size(Decimal("3000000")) == Decimal("1000")
    assert tick_size(Decimal("1500000")) == Decimal("1000")
    assert tick_size(Decimal("700000")) == Decimal("500")
    assert tick_size(Decimal("50000")) == Decimal("10")
    assert tick_size(Decimal("500")) == Decimal("1")
    assert tick_size(Decimal("50")) == Decimal("0.1")
    assert tick_size(Decimal("5")) == Decimal("0.01")
    assert tick_size(Decimal("0.5")) == Decimal("0.001")


def test_align_price_rejects_arbitrary_decimals():
    assert align_price(Decimal("50000123.4567")) == Decimal("50000000")
    assert align_price(Decimal("1234.6")) == Decimal("1235")


def test_fee_is_five_bps():
    assert fee(Decimal("1000000")) == Decimal("500")
    assert round_trip_cost(Decimal("1000000")) == Decimal("1000")


def test_min_order_amount():
    assert not is_orderable(MIN_ORDER_KRW - 1)
    assert is_orderable(MIN_ORDER_KRW)
