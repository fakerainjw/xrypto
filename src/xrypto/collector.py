"""증분 캔들 수집기.

핵심 규칙:

* 항상 증분 수집한다. 마지막 저장 시각 **이후** 구간만 채우므로, cron이 밀리거나
  실행이 통째로 스킵돼도 다음 실행이 빈 구간을 그대로 메운다 (자동 복구).
* 미완성 캔들(마감 전 봉)은 저장하지 않는다.
* 업비트는 ``since``가 없고 ``to``(exclusive) 기준 역방향 페이지네이션만 되므로,
  목표 구간의 **끝에서부터** 과거로 훑어 내려간다.
* 거래가 없는 구간은 캔들이 아예 누락된다. 응답이 비었다고 해서 구간이 끝난 게
  아니므로, 요청한 ``to``를 기준으로 창을 옮겨 가며 계속 내려간다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from xrypto.storage import ParquetStore, normalize_candle
from xrypto.timeutil import as_utc, is_closed, last_closed_candle_start, now_utc, parse_upbit_utc
from xrypto.upbit.client import MAX_CANDLE_COUNT, UpbitClient

log = logging.getLogger(__name__)

#: 저장된 데이터가 전혀 없을 때 처음 채워 넣을 기간.
DEFAULT_INITIAL_DAYS = 2

#: 한 마켓·한 실행에서 허용할 최대 호출 수. 과거 대량 백필은 후순위이므로
#: 한 번의 cron 실행이 폭주하지 않도록 막아 둔다.
DEFAULT_MAX_PAGES = 20


@dataclass(frozen=True)
class CollectResult:
    market: str
    unit: int
    fetched: int
    written: int
    start: datetime | None
    end: datetime | None
    pages: int
    truncated: bool  # max_pages에 걸려 구간을 다 못 채웠는지

    def __str__(self) -> str:
        if self.start is None:
            return f"{self.market} {self.unit}m: up to date"
        span = f"{self.start:%Y-%m-%d %H:%M} .. {self.end:%Y-%m-%d %H:%M} UTC"
        tail = " (truncated, will resume next run)" if self.truncated else ""
        return (
            f"{self.market} {self.unit}m: +{self.written} rows "
            f"({self.fetched} fetched, {self.pages} calls) {span}{tail}"
        )


def collect_market(
    client: UpbitClient,
    store: ParquetStore,
    market: str,
    unit: int,
    *,
    now: datetime | None = None,
    initial_days: int = DEFAULT_INITIAL_DAYS,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> CollectResult:
    """한 마켓의 미수집 구간을 채운다."""
    now = as_utc(now) if now else now_utc()
    step = timedelta(minutes=unit)

    end = last_closed_candle_start(now, unit)
    last = store.last_ts(market, unit)
    start = (last + step) if last is not None else end - timedelta(days=initial_days)

    if start > end:
        return CollectResult(market, unit, 0, 0, None, None, 0, False)

    collected: dict[datetime, dict] = {}
    # `to`는 exclusive이므로 end 봉을 받으려면 한 칸 뒤를 가리킨다.
    cursor = end + step
    pages = 0
    truncated = False

    while cursor > start:
        if pages >= max_pages:
            truncated = True
            log.info("%s %sm: hit max_pages=%d, resuming next run", market, unit, max_pages)
            break
        raw = client.get_candles(market, unit, to=cursor, count=MAX_CANDLE_COUNT)
        pages += 1

        for item in raw:
            ts = parse_upbit_utc(item["candle_date_time_utc"])
            if ts < start or ts > end:
                continue
            if not is_closed(ts, unit, now):
                continue  # 미완성 캔들은 버린다
            collected[ts] = normalize_candle(item)

        # 업비트는 `to` 이전에 실제로 존재하는 봉을 최대 count개 돌려준다.
        # 거래가 없어 비어 있는 구간은 응답에서 그냥 빠질 뿐 페이지를 끊지 않으므로,
        # 응답이 count개 미만이면 그 마켓의 과거 데이터가 바닥난 것이다.
        if len(raw) < MAX_CANDLE_COUNT:
            log.debug("%s %sm: history exhausted before %s", market, unit, start)
            break
        # `to`는 exclusive이므로 가장 오래된 봉을 그대로 다음 커서로 쓴다.
        cursor = min(parse_upbit_utc(item["candle_date_time_utc"]) for item in raw)

    rows = [collected[ts] for ts in sorted(collected)]
    written = store.write_candles(market, unit, rows) if rows else 0
    return CollectResult(
        market=market,
        unit=unit,
        fetched=len(rows),
        written=written,
        start=start,
        end=end,
        pages=pages,
        truncated=truncated,
    )


def collect_markets(
    client: UpbitClient,
    store: ParquetStore,
    markets: list[str],
    unit: int,
    *,
    now: datetime | None = None,
    initial_days: int = DEFAULT_INITIAL_DAYS,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> list[CollectResult]:
    """여러 마켓을 순서대로 수집. 한 마켓이 실패해도 나머지는 계속 진행한다."""
    results: list[CollectResult] = []
    for market in markets:
        result = collect_market(
            client,
            store,
            market,
            unit,
            now=now,
            initial_days=initial_days,
            max_pages=max_pages,
        )
        log.info("%s", result)
        results.append(result)
    return results
