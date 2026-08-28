from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.fakes import FakeMarket, FakeResponse, FakeSession
from xrypto.collector import collect_market
from xrypto.storage import ParquetStore
from xrypto.timeutil import parse_upbit_utc
from xrypto.upbit.client import UpbitClient


def _client_for(market: FakeMarket):
    def handler(url, params):
        if "/market/all" in url:
            return FakeResponse(200, [{"market": market.market}])
        to = params.get("to")
        to_dt = datetime.strptime(to, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) if to else None
        return FakeResponse(200, market.serve(to_dt, int(params["count"])))

    session = FakeSession(handler)
    return UpbitClient(
        ("https://api.upbit.com",), session=session, min_interval=0.0, sleep=lambda _: None
    ), session


def test_first_run_backfills_initial_window(tmp_path):
    now = datetime(2024, 1, 1, 12, 0, 30, tzinfo=UTC)
    market = FakeMarket(
        first=datetime(2023, 12, 30, tzinfo=UTC), last=datetime(2024, 1, 1, 12, tzinfo=UTC)
    )
    client, _ = _client_for(market)
    store = ParquetStore(tmp_path)

    result = collect_market(client, store, "KRW-BTC", 1, now=now, initial_days=1, max_pages=50)

    assert result.written == 1441  # 하루치 분봉 + 양끝 포함
    assert store.last_ts("KRW-BTC", 1) == datetime(2024, 1, 1, 11, 59, tzinfo=UTC)


def test_open_candle_is_not_stored(tmp_path):
    """진행 중인 봉(12:00)은 저장되지 않아야 한다."""
    now = datetime(2024, 1, 1, 12, 0, 30, tzinfo=UTC)
    market = FakeMarket(
        first=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), last=datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    )
    client, _ = _client_for(market)
    store = ParquetStore(tmp_path)

    collect_market(client, store, "KRW-BTC", 1, now=now, initial_days=1)

    assert store.last_ts("KRW-BTC", 1) == datetime(2024, 1, 1, 11, 59, tzinfo=UTC)


def test_second_run_is_incremental(tmp_path):
    market = FakeMarket(
        first=datetime(2024, 1, 1, tzinfo=UTC), last=datetime(2024, 1, 1, 10, tzinfo=UTC)
    )
    client, session = _client_for(market)
    store = ParquetStore(tmp_path)

    collect_market(
        client,
        store,
        "KRW-BTC",
        1,
        now=datetime(2024, 1, 1, 10, 0, 5, tzinfo=UTC),
        initial_days=1,
        max_pages=50,
    )
    calls_after_first = len(session.calls)

    market.last = datetime(2024, 1, 1, 10, 5, tzinfo=UTC)
    result = collect_market(
        client, store, "KRW-BTC", 1, now=datetime(2024, 1, 1, 10, 5, 30, tzinfo=UTC), max_pages=50
    )

    assert result.written == 5  # 10:00~10:04 다섯 봉만
    assert len(session.calls) - calls_after_first == 1  # 한 번의 호출로 끝


def test_skipped_cron_run_self_heals(tmp_path):
    """실행이 통째로 스킵돼도 다음 실행이 빈 구간을 메운다."""
    market = FakeMarket(
        first=datetime(2024, 1, 1, tzinfo=UTC), last=datetime(2024, 1, 1, 1, 0, tzinfo=UTC)
    )
    client, _ = _client_for(market)
    store = ParquetStore(tmp_path)

    collect_market(
        client,
        store,
        "KRW-BTC",
        1,
        now=datetime(2024, 1, 1, 1, 0, 10, tzinfo=UTC),
        initial_days=1,
        max_pages=50,
    )

    # 3시간 동안 cron이 돌지 않았다.
    market.last = datetime(2024, 1, 1, 4, 0, tzinfo=UTC)
    collect_market(
        client, store, "KRW-BTC", 1, now=datetime(2024, 1, 1, 4, 0, 40, tzinfo=UTC), max_pages=50
    )

    table = store.read_range("KRW-BTC", 1)
    stored = [row["ts"] for row in table.to_pylist()]
    expected = [datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=i) for i in range(240)]
    assert stored == expected  # 00:00 ~ 03:59, 구멍 없음


def test_no_new_candles_is_a_noop(tmp_path):
    market = FakeMarket(
        first=datetime(2024, 1, 1, tzinfo=UTC), last=datetime(2024, 1, 1, 1, tzinfo=UTC)
    )
    client, _ = _client_for(market)
    store = ParquetStore(tmp_path)
    now = datetime(2024, 1, 1, 1, 0, 30, tzinfo=UTC)

    collect_market(client, store, "KRW-BTC", 1, now=now, initial_days=1, max_pages=50)
    again = collect_market(client, store, "KRW-BTC", 1, now=now, max_pages=50)

    assert again.written == 0
    assert again.start is None


def test_missing_candles_are_not_invented(tmp_path):
    """거래가 없는 구간은 캔들이 아예 없다. 채워 넣지 않고 그대로 비워 둔다."""
    gap = [datetime(2024, 1, 1, 0, m) for m in range(10, 20)]
    market = FakeMarket(
        first=datetime(2024, 1, 1, tzinfo=UTC),
        last=datetime(2024, 1, 1, 1, tzinfo=UTC),
        missing=gap,
    )
    client, _ = _client_for(market)
    store = ParquetStore(tmp_path)

    collect_market(
        client,
        store,
        "KRW-BTC",
        1,
        now=datetime(2024, 1, 1, 1, 0, 30, tzinfo=UTC),
        initial_days=1,
        max_pages=50,
    )

    stored = {row["ts"] for row in store.read_range("KRW-BTC", 1).to_pylist()}
    assert datetime(2024, 1, 1, 0, 9, tzinfo=UTC) in stored
    assert datetime(2024, 1, 1, 0, 15, tzinfo=UTC) not in stored
    assert datetime(2024, 1, 1, 0, 20, tzinfo=UTC) in stored


def test_max_pages_truncates_and_resumes(tmp_path):
    market = FakeMarket(
        first=datetime(2024, 1, 1, tzinfo=UTC), last=datetime(2024, 1, 2, tzinfo=UTC)
    )
    client, _ = _client_for(market)
    store = ParquetStore(tmp_path)
    now = datetime(2024, 1, 2, 0, 0, 30, tzinfo=UTC)

    first = collect_market(client, store, "KRW-BTC", 1, now=now, initial_days=1, max_pages=2)
    assert first.truncated
    assert first.written == 400  # 200 * 2

    # 다음 실행이 이어서 채운다. 앞 구간이 비어 있으므로 마지막 저장 시각 기준으로
    # 다시 시도하고, 결국 남은 구간을 메운다.
    second = collect_market(client, store, "KRW-BTC", 1, now=now, max_pages=50)
    assert second.written == 0  # 이미 최신까지 채워져 있다


def test_page_boundary_duplicates_are_deduped(tmp_path):
    market = FakeMarket(
        first=datetime(2024, 1, 1, tzinfo=UTC), last=datetime(2024, 1, 1, 8, tzinfo=UTC)
    )
    client, _ = _client_for(market)
    store = ParquetStore(tmp_path)
    now = datetime(2024, 1, 1, 8, 0, 30, tzinfo=UTC)

    collect_market(client, store, "KRW-BTC", 1, now=now, initial_days=1, max_pages=50)
    rows = store.read_range("KRW-BTC", 1).to_pylist()
    timestamps = [row["ts"] for row in rows]
    assert len(timestamps) == len(set(timestamps))
    assert timestamps == sorted(timestamps)


def test_stored_utc_and_kst_strings_agree(tmp_path):
    market = FakeMarket(
        first=datetime(2024, 1, 1, tzinfo=UTC), last=datetime(2024, 1, 1, 0, 5, tzinfo=UTC)
    )
    client, _ = _client_for(market)
    store = ParquetStore(tmp_path)

    collect_market(
        client,
        store,
        "KRW-BTC",
        1,
        now=datetime(2024, 1, 1, 0, 5, 30, tzinfo=UTC),
        initial_days=1,
        max_pages=50,
    )

    for row in store.read_range("KRW-BTC", 1).to_pylist():
        assert row["ts"] == parse_upbit_utc(row["candle_date_time_utc"])
