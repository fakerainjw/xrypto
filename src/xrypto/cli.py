"""커맨드라인 진입점.

uv run xrypto markets
uv run xrypto collect --unit 1 --markets KRW-BTC,KRW-ETH
uv run xrypto status --unit 1
"""

from __future__ import annotations

import argparse
import logging
import sys

from xrypto.collector import (
    DEFAULT_INITIAL_DAYS,
    DEFAULT_MAX_PAGES,
    collect_markets,
)
from xrypto.storage import ParquetStore
from xrypto.timeutil import to_kst
from xrypto.upbit.client import QUOTATION_HOSTS, UpbitBlockedError, UpbitClient, UpbitError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xrypto", description="Upbit candle collector")
    parser.add_argument("--data-dir", default="data", help="parquet 루트 (기본 data)")
    parser.add_argument("--quote", default="KRW", help="기준 마켓 (기본 KRW)")
    parser.add_argument("--verbose", "-v", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("markets", help="마켓 코드 목록 조회")

    collect = sub.add_parser("collect", help="증분 캔들 수집")
    collect.add_argument("--unit", type=int, default=1, help="분봉 단위 (기본 1)")
    collect.add_argument(
        "--markets",
        default="",
        help="쉼표로 구분한 마켓 코드. 비우면 마켓 조회 API로 전부 받아온다",
    )
    collect.add_argument("--limit", type=int, default=0, help="처리할 마켓 수 상한 (0=제한 없음)")
    collect.add_argument("--initial-days", type=int, default=DEFAULT_INITIAL_DAYS)
    collect.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)

    status = sub.add_parser("status", help="저장된 마지막 캔들 시각 확인")
    status.add_argument("--unit", type=int, default=1)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    store = ParquetStore(args.data_dir)

    if args.command == "status":
        directory = store.root
        if not directory.is_dir():
            print("no data yet")
            return 0
        for market_dir in sorted(p for p in directory.iterdir() if p.is_dir()):
            last = store.last_ts(market_dir.name, args.unit)
            if last is None:
                continue
            print(
                f"{market_dir.name} {args.unit}m  last={last:%Y-%m-%d %H:%M}Z "
                f"({to_kst(last):%Y-%m-%d %H:%M} KST)"
            )
        return 0

    client = UpbitClient(QUOTATION_HOSTS)

    try:
        if args.command == "markets":
            for code in client.get_markets(args.quote):
                print(code)
            return 0

        markets = [m.strip() for m in args.markets.split(",") if m.strip()]
        if not markets:
            # 심볼은 하드코딩하지 않는다.
            markets = client.get_markets(args.quote)
        if args.limit:
            markets = markets[: args.limit]

        results = collect_markets(
            client,
            store,
            markets,
            args.unit,
            initial_days=args.initial_days,
            max_pages=args.max_pages,
        )
        total = sum(r.written for r in results)
        print(f"collected {total} new candles across {len(results)} markets")
        return 0
    except UpbitBlockedError as exc:
        # 418. 계속 두드리면 차단 시간만 길어진다.
        logging.error("blocked by upbit, stopping: %s", exc)
        return 2
    except UpbitError as exc:
        logging.error("upbit api error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
