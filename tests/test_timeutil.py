from __future__ import annotations

from datetime import UTC, datetime

from xrypto.timeutil import (
    KST,
    floor_to_unit,
    is_closed,
    last_closed_candle_start,
    month_key,
    parse_upbit_kst,
    parse_upbit_utc,
    to_kst,
    to_upbit_param,
)


def test_naive_utc_string_is_parsed_as_utc():
    dt = parse_upbit_utc("2024-03-01T04:05:00")
    assert dt == datetime(2024, 3, 1, 4, 5, tzinfo=UTC)
    assert dt.tzinfo is not None


def test_kst_string_parses_to_the_same_instant():
    """같은 봉의 utc/kst 문자열은 같은 순간이어야 한다 (9시간 어긋나면 실패)."""
    assert parse_upbit_utc("2024-03-01T00:00:00") == parse_upbit_kst("2024-03-01T09:00:00")


def test_to_kst_shifts_only_for_display():
    assert to_kst(datetime(2024, 3, 1, tzinfo=UTC)).hour == 9
    assert to_kst(datetime(2024, 3, 1, tzinfo=UTC)).tzinfo is KST


def test_to_upbit_param_is_utc_zulu():
    assert to_upbit_param(datetime(2024, 3, 1, 9, tzinfo=KST)) == "2024-03-01T00:00:00Z"


def test_floor_to_unit():
    dt = datetime(2024, 3, 1, 4, 37, 12, tzinfo=UTC)
    assert floor_to_unit(dt, 1) == datetime(2024, 3, 1, 4, 37, tzinfo=UTC)
    assert floor_to_unit(dt, 5) == datetime(2024, 3, 1, 4, 35, tzinfo=UTC)
    assert floor_to_unit(dt, 60) == datetime(2024, 3, 1, 4, 0, tzinfo=UTC)


def test_last_closed_candle_start_excludes_the_open_bar():
    now = datetime(2024, 3, 1, 4, 37, 12, tzinfo=UTC)
    assert last_closed_candle_start(now, 1) == datetime(2024, 3, 1, 4, 36, tzinfo=UTC)
    assert last_closed_candle_start(now, 5) == datetime(2024, 3, 1, 4, 30, tzinfo=UTC)


def test_is_closed_boundary():
    start = datetime(2024, 3, 1, 4, 0, tzinfo=UTC)
    assert not is_closed(start, 5, datetime(2024, 3, 1, 4, 4, 59, tzinfo=UTC))
    assert is_closed(start, 5, datetime(2024, 3, 1, 4, 5, tzinfo=UTC))


def test_month_key_uses_utc():
    # KST로 3월 1일이지만 UTC로는 2월이다.
    assert month_key(datetime(2024, 3, 1, 5, tzinfo=KST)) == "2024-02"
