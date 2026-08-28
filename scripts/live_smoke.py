"""실제 업비트 API를 때려 보는 스모크 테스트.

개발 샌드박스는 egress 정책 때문에 upbit.com에 나갈 수 없다. 이 스크립트는
GitHub Actions 러너에서 돌리기 위한 것으로, 수집기가 실제로 실행될 환경
(해외 IP)에서 다음을 검증한다.

* 어느 엔드포인트가 선택되는지 (국내 차단 시 글로벌 폴백이 실제로 도는지)
* 응답 필드 이름과 타입이 저장 스키마와 맞는지
* `candle_date_time_utc` / `kst`가 정확히 9시간 차이인지
* 역방향 페이지네이션이 실제로 더 과거를 돌려주는지
* `Remaining-Req` 헤더에 `sec`가 실제로 오는지
* 미완성 캔들이 저장되지 않는지, 두 번째 실행이 증분으로만 도는지

성공/실패를 종료 코드로 알리고, 원본 응답을 fixtures/로 덤프한다.
덤프한 파일은 네트워크 없는 환경에서 회귀 테스트 픽스처로 쓸 수 있다.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from xrypto.collector import collect_market
from xrypto.storage import ParquetStore
from xrypto.timeutil import now_utc, parse_upbit_kst, parse_upbit_utc
from xrypto.upbit.client import QUOTATION_HOSTS, UpbitClient, UpbitError

FIXTURE_DIR = Path("fixtures")
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}{f' — {detail}' if detail else ''}")
    if not condition:
        FAILURES.append(label)


def main() -> int:
    FIXTURE_DIR.mkdir(exist_ok=True)
    client = UpbitClient(QUOTATION_HOSTS)

    # 1. 엔드포인트 판별 --------------------------------------------------
    try:
        base = client.base_url
    except UpbitError as exc:
        print(f"[FAIL] 업비트에 나갈 수 없다 — {exc}")
        print(
            "\n이 환경의 egress 정책이 upbit.com을 막고 있을 수 있다.\n"
            "GitHub Actions 러너에서 돌리거나, 클라우드 환경의 Network access를\n"
            "Custom으로 바꾸고 api.upbit.com / sg-api.upbit.com을 허용할 것."
        )
        return 1
    print(f"\n== endpoint: {base}")
    check("접근 가능한 엔드포인트를 찾았다", base in QUOTATION_HOSTS, base)
    if base != QUOTATION_HOSTS[0]:
        print(f"    (국내 {QUOTATION_HOSTS[0]}가 막혀 글로벌로 폴백했다)")

    # 2. 마켓 목록 --------------------------------------------------------
    markets = client.get_markets("KRW")
    print(f"\n== KRW markets: {len(markets)}")
    check("마켓 목록이 비어 있지 않다", len(markets) > 0, f"{len(markets)}개")
    check("모두 KRW- 접두사다", all(m.startswith("KRW-") for m in markets))
    check("KRW-BTC가 있다", "KRW-BTC" in markets)
    (FIXTURE_DIR / "markets.json").write_text(json.dumps(markets, indent=2))

    # 3. 캔들 응답 형태 ----------------------------------------------------
    raw = client.get_candles("KRW-BTC", 1, count=5)
    print(f"\n== candles: {len(raw)}")
    print(json.dumps(raw[0], indent=2, default=str, ensure_ascii=False))
    (FIXTURE_DIR / "candles_krw_btc_1m.json").write_text(
        json.dumps(raw, indent=2, default=str, ensure_ascii=False)
    )

    expected_fields = {
        "market",
        "candle_date_time_utc",
        "candle_date_time_kst",
        "opening_price",
        "high_price",
        "low_price",
        "trade_price",
        "timestamp",
        "candle_acc_trade_price",
        "candle_acc_trade_volume",
        "unit",
    }
    missing = expected_fields - set(raw[0])
    check("저장 스키마가 기대하는 필드가 모두 있다", not missing, f"누락: {sorted(missing)}")

    unknown = set(raw[0]) - expected_fields
    if unknown:
        print(f"    (스키마에 없는 새 필드 발견 → extra 컬럼으로 보존됨: {sorted(unknown)})")

    check(
        "가격이 float이 아니라 Decimal로 파싱된다",
        isinstance(raw[0]["trade_price"], (Decimal, int)),
        type(raw[0]["trade_price"]).__name__,
    )
    check(
        "누적 거래대금이 float이 아니다",
        isinstance(raw[0]["candle_acc_trade_price"], (Decimal, int)),
        type(raw[0]["candle_acc_trade_price"]).__name__,
    )

    # 4. utc / kst 정합성 --------------------------------------------------
    utc = parse_upbit_utc(raw[0]["candle_date_time_utc"])
    kst = parse_upbit_kst(raw[0]["candle_date_time_kst"])
    check("utc/kst 문자열이 같은 순간을 가리킨다", utc == kst, f"{utc} vs {kst}")

    # 5. 응답 정렬 및 역방향 페이지네이션 -----------------------------------
    times = [parse_upbit_utc(c["candle_date_time_utc"]) for c in raw]
    check("응답이 최신 → 과거 순이다", times == sorted(times, reverse=True))

    oldest = min(times)
    older = client.get_candles("KRW-BTC", 1, to=oldest, count=5)
    older_times = [parse_upbit_utc(c["candle_date_time_utc"]) for c in older]
    check(
        "`to`가 exclusive다 (경계 봉이 다시 오지 않는다)",
        oldest not in older_times,
        f"to={oldest}",
    )
    check("역방향 페이지네이션이 더 과거를 돌려준다", max(older_times) < oldest)

    # 6. Remaining-Req 헤더 -------------------------------------------------
    res = client.session.get(f"{base}/v1/market/all", params={"isDetails": "false"}, timeout=10)
    remaining = res.headers.get("Remaining-Req")
    print(f"\n== Remaining-Req: {remaining!r}")
    check("Remaining-Req 헤더가 온다", bool(remaining))
    if remaining:
        check("sec 값을 파싱할 수 있다", UpbitClient._remaining_sec(res.headers) is not None)

    # 7. 실제 수집 왕복 ------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        store = ParquetStore(tmp)
        now = now_utc()
        first = collect_market(client, store, "KRW-BTC", 1, now=now, initial_days=1, max_pages=10)
        print(f"\n== first collect: {first}")
        check("첫 수집이 캔들을 저장했다", first.written > 0, f"{first.written}행")

        last = store.last_ts("KRW-BTC", 1)
        check(
            "미완성 캔들이 저장되지 않았다",
            last is not None and last + timedelta(minutes=1) <= now,
            f"last={last}, now={now}",
        )

        second = collect_market(client, store, "KRW-BTC", 1, now=now, max_pages=10)
        print(f"== second collect: {second}")
        check("두 번째 실행이 증분으로 아무것도 안 받는다", second.written == 0)

        table = store.read_range("KRW-BTC", 1)
        stamps = [row["ts"] for row in table.to_pylist()]
        check("중복이 없다", len(stamps) == len(set(stamps)))
        check("ts 오름차순으로 저장됐다", stamps == sorted(stamps))

        prices = [row["trade_price"] for row in table.to_pylist()]
        check("읽어도 Decimal이다", all(isinstance(p, Decimal) for p in prices))

    # ----------------------------------------------------------------------
    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): {FAILURES}")
        return 1
    print("all live checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
