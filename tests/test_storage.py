from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from xrypto.storage import ParquetStore, normalize_candle, to_decimal


def _raw(ts: datetime, market="KRW-BTC", unit=1, trade_price="50000000.5", extra=None):
    payload = {
        "market": market,
        "candle_date_time_utc": ts.strftime("%Y-%m-%dT%H:%M:%S"),
        "candle_date_time_kst": ts.strftime("%Y-%m-%dT%H:%M:%S"),
        "opening_price": Decimal("50000000"),
        "high_price": Decimal("50100000"),
        "low_price": Decimal("49900000"),
        "trade_price": Decimal(trade_price),
        "timestamp": int(ts.timestamp() * 1000),
        "candle_acc_trade_price": Decimal("123456789.123456789012"),
        "candle_acc_trade_volume": Decimal("0.12345678"),
        "unit": unit,
    }
    payload.update(extra or {})
    return payload


def test_path_layout(tmp_path):
    store = ParquetStore(tmp_path)
    path = store.path_for("KRW-BTC", 1, datetime(2024, 3, 15, tzinfo=UTC))
    assert path == tmp_path / "KRW-BTC" / "1m" / "2024-03.parquet"


def test_decimal_round_trips_without_float_error(tmp_path):
    store = ParquetStore(tmp_path)
    ts = datetime(2024, 3, 1, tzinfo=UTC)
    store.write_candles("KRW-BTC", 1, [normalize_candle(_raw(ts))])

    table = store.read_month("KRW-BTC", 1, ts)
    row = table.to_pylist()[0]
    assert isinstance(row["trade_price"], Decimal)
    assert row["trade_price"] == Decimal("50000000.5")
    # 누적 거래대금은 특히 보존돼야 한다.
    assert row["candle_acc_trade_price"] == Decimal("123456789.123456789012")


def test_dedup_by_ts_and_new_data_wins(tmp_path):
    store = ParquetStore(tmp_path)
    ts = datetime(2024, 3, 1, 0, 0, tzinfo=UTC)
    store.write_candles("KRW-BTC", 1, [normalize_candle(_raw(ts, trade_price="100"))])
    added = store.write_candles("KRW-BTC", 1, [normalize_candle(_raw(ts, trade_price="200"))])

    assert added == 0  # 같은 ts라 행이 늘지 않는다
    rows = store.read_month("KRW-BTC", 1, ts).to_pylist()
    assert len(rows) == 1
    assert rows[0]["trade_price"] == Decimal("200")


def test_rows_split_across_months(tmp_path):
    store = ParquetStore(tmp_path)
    jan = datetime(2024, 1, 31, 23, 59, tzinfo=UTC)
    feb = datetime(2024, 2, 1, 0, 0, tzinfo=UTC)
    store.write_candles("KRW-BTC", 1, [normalize_candle(_raw(jan)), normalize_candle(_raw(feb))])

    assert (tmp_path / "KRW-BTC" / "1m" / "2024-01.parquet").exists()
    assert (tmp_path / "KRW-BTC" / "1m" / "2024-02.parquet").exists()
    assert store.last_ts("KRW-BTC", 1) == feb


def test_last_ts_none_when_empty(tmp_path):
    assert ParquetStore(tmp_path).last_ts("KRW-BTC", 1) is None


def test_unknown_fields_are_preserved_in_extra(tmp_path):
    store = ParquetStore(tmp_path)
    ts = datetime(2024, 3, 1, tzinfo=UTC)
    row = normalize_candle(_raw(ts, extra={"brand_new_field": "keep me"}))
    store.write_candles("KRW-BTC", 1, [row])
    stored = store.read_month("KRW-BTC", 1, ts).to_pylist()[0]
    assert "keep me" in stored["extra"]


def test_read_range_is_sorted_and_bounded(tmp_path):
    store = ParquetStore(tmp_path)
    times = [datetime(2024, 3, 1, 0, m, tzinfo=UTC) for m in range(5)]
    store.write_candles("KRW-BTC", 1, [normalize_candle(_raw(t)) for t in reversed(times)])
    table = store.read_range("KRW-BTC", 1, start=times[1], end=times[3])
    got = [r["ts"] for r in table.to_pylist()]
    assert got == times[1:4]


def test_to_decimal_never_goes_through_float():
    assert to_decimal(0.1) == Decimal("0.1")
    assert to_decimal("1e-20") == Decimal("0E-18")  # 스케일 초과분만 반올림
