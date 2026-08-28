from __future__ import annotations

import json

import pytest

from tests.fakes import FakeResponse, FakeSession
from xrypto.upbit.client import UpbitBlockedError, UpbitClient, UpbitError


def _client(handler, **kwargs):
    slept: list[float] = []
    client = UpbitClient(
        ("https://api.upbit.com", "https://sg-api.upbit.com"),
        session=FakeSession(handler),
        min_interval=0.0,
        sleep=slept.append,
        **kwargs,
    )
    return client, slept


def test_origin_header_is_never_sent():
    client, _ = _client(lambda url, params: FakeResponse(200, [{"market": "KRW-BTC"}]))
    assert "Origin" not in client.session.headers


def test_falls_back_to_global_endpoint_when_domestic_is_blocked():
    def handler(url, params):
        if url.startswith("https://api.upbit.com"):
            return FakeResponse(403, {"error": "region"})
        return FakeResponse(200, [{"market": "KRW-BTC"}, {"market": "BTC-ETH"}])

    client, _ = _client(handler)
    assert client.base_url == "https://sg-api.upbit.com"


def test_no_reachable_endpoint_raises():
    client, _ = _client(lambda url, params: FakeResponse(500))
    with pytest.raises(UpbitError):
        _ = client.base_url


def test_418_stops_immediately_without_retry():
    calls = {"n": 0}

    def handler(url, params):
        if "/market/all" in url and calls["n"] == 0:
            calls["n"] += 1
            return FakeResponse(200, [{"market": "KRW-BTC"}])
        calls["n"] += 1
        return FakeResponse(418, {"error": "blocked"})

    client, _ = _client(handler)
    with pytest.raises(UpbitBlockedError):
        client.get_candles("KRW-BTC", 1)
    assert calls["n"] == 2  # 판별 1회 + 캔들 1회, 재시도 없음


def test_429_backs_off_exponentially_then_succeeds():
    state = {"n": 0}

    def handler(url, params):
        if "/market/all" in url:
            return FakeResponse(200, [{"market": "KRW-BTC"}])
        state["n"] += 1
        if state["n"] <= 2:
            return FakeResponse(429, {"error": "rate limit"})
        return FakeResponse(200, [{"market": "KRW-BTC"}])

    client, slept = _client(handler)
    assert client.get_candles("KRW-BTC", 1) == [{"market": "KRW-BTC"}]
    backoffs = [s for s in slept if s >= 1.0]
    assert len(backoffs) == 2
    assert backoffs[1] > backoffs[0]  # 지수 증가


def test_remaining_req_reads_sec_not_min():
    header = {"Remaining-Req": "group=default; min=1799; sec=0"}

    def handler(url, params):
        return FakeResponse(200, [{"market": "KRW-BTC"}], headers=header)

    client, slept = _client(handler)
    client.get_markets()
    assert 1.0 in slept  # sec가 소진되어 다음 창까지 양보


def test_prices_are_parsed_as_decimal_not_float():
    body = json.dumps(
        [{"market": "KRW-BTC", "trade_price": 0.1, "candle_acc_trade_price": 123456789.123456789}]
    )

    def handler(url, params):
        if "/market/all" in url:
            return FakeResponse(200, [{"market": "KRW-BTC"}])
        return FakeResponse(200, text=body)

    client, _ = _client(handler)
    candle = client.get_candles("KRW-BTC", 1)[0]
    from decimal import Decimal

    assert candle["trade_price"] == Decimal("0.1")
    assert isinstance(candle["candle_acc_trade_price"], Decimal)


def test_get_markets_filters_by_quote_and_uses_upbit_codes():
    def handler(url, params):
        return FakeResponse(
            200, [{"market": "KRW-BTC"}, {"market": "BTC-ETH"}, {"market": "KRW-ETH"}]
        )

    client, _ = _client(handler)
    assert client.get_markets("KRW") == ["KRW-BTC", "KRW-ETH"]


def test_candle_count_cap():
    client, _ = _client(lambda url, params: FakeResponse(200, []))
    with pytest.raises(ValueError):
        client.get_candles("KRW-BTC", 1, count=201)
