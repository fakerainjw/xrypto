from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta


class FakeResponse:
    def __init__(self, status_code: int, payload=None, headers=None, text: str | None = None):
        self.status_code = status_code
        self.headers = headers or {}
        if text is None:
            text = json.dumps(payload if payload is not None else [])
        self._text = text

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def text(self) -> str:
        return self._text

    def json(self):
        return json.loads(self._text)


class FakeSession:
    """requests.Session 대역. 호출 기록을 남긴다."""

    def __init__(self, handler):
        self.headers: dict[str, str] = {}
        self.handler = handler
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self.handler(url, dict(params or {}))


class FakeMarket:
    """분봉을 생성해 주는 가짜 업비트 마켓."""

    def __init__(self, market="KRW-BTC", unit=1, first=None, last=None, missing=()):
        self.market = market
        self.unit = unit
        self.first = first or datetime(2024, 1, 1, tzinfo=UTC)
        self.last = last or datetime(2024, 1, 1, 12, tzinfo=UTC)
        self.missing = {m.replace(tzinfo=UTC) for m in missing}

    def candle_times(self):
        step = timedelta(minutes=self.unit)
        out = []
        cur = self.first
        while cur <= self.last:
            if cur not in self.missing:
                out.append(cur)
            cur += step
        return out

    def candle(self, ts: datetime) -> dict:
        idx = int(ts.timestamp()) // 60
        price = 50000000 + (idx % 100) * 1000
        return {
            "market": self.market,
            "candle_date_time_utc": ts.strftime("%Y-%m-%dT%H:%M:%S"),
            "candle_date_time_kst": (ts + timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%S"),
            "opening_price": price,
            "high_price": price + 500,
            "low_price": price - 500,
            "trade_price": price + 100,
            "timestamp": int(ts.timestamp() * 1000) + 59999,
            "candle_acc_trade_price": 12345678.90123456789,
            "candle_acc_trade_volume": 0.12345678,
            "unit": self.unit,
        }

    def serve(self, to: datetime | None, count: int) -> list[dict]:
        times = [t for t in self.candle_times() if to is None or t < to]
        times = sorted(times, reverse=True)[:count]
        return [self.candle(t) for t in times]
