# xrypto

업비트(Upbit) 시세 데이터 수집 및 백테스트 프로젝트.

프로젝트 규칙과 업비트 API 주의사항은 [CLAUDE.md](CLAUDE.md)에 정리되어 있다.

## 무엇을 하나

- 업비트 KRW 마켓 분봉을 **증분**으로 수집한다 (마지막 저장 시각 이후만).
- GitHub Actions cron으로 주기 실행하며, 실행이 밀리거나 스킵돼도 다음 실행이
  빈 구간을 자동으로 메운다.
- 월별 parquet으로 저장한다: `data/{MARKET}/{UNIT}m/YYYY-MM.parquet`
- 가격·금액은 `Decimal`(parquet `decimal128`)로 저장해 float 오차가 끼지 않는다.

## 설치

```bash
uv sync --all-groups
```

## 사용

```bash
# KRW 마켓 코드 목록 (하드코딩하지 않고 API로 받아온다)
uv run xrypto markets

# 1분봉 증분 수집 (마켓 생략 시 KRW 마켓 전체)
uv run xrypto collect --unit 1 --markets KRW-BTC,KRW-ETH

# 저장 현황 확인
uv run xrypto status --unit 1
```

주요 옵션:

| 옵션 | 설명 |
| --- | --- |
| `--data-dir` | parquet 루트 (기본 `data`) |
| `--unit` | 분봉 단위 (1, 3, 5, 15, 30, 60, 240) |
| `--markets` | 쉼표로 구분한 마켓 코드. 비우면 전체 |
| `--limit` | 처리할 마켓 수 상한 |
| `--initial-days` | 저장된 데이터가 없을 때 처음 채울 기간 (기본 2일) |
| `--max-pages` | 마켓당 한 실행에서 허용할 최대 API 호출 수 (기본 20) |

## 저장 스키마

| 컬럼 | 타입 | 비고 |
| --- | --- | --- |
| `ts` | `timestamp[us, UTC]` | 봉 시작 시각. 정렬·중복 제거 기준 |
| `market` | `string` | `KRW-BTC` 형식 |
| `candle_date_time_utc` / `candle_date_time_kst` | `string` | 원본 응답 그대로 |
| `opening_price` / `high_price` / `low_price` / `trade_price` | `decimal128(38,18)` | |
| `timestamp` | `int64` | 원본 응답 그대로 (ms) |
| `candle_acc_trade_price` | `decimal128(38,18)` | 누적 거래대금 |
| `candle_acc_trade_volume` | `decimal128(38,18)` | |
| `unit` | `int32` | |
| `extra` | `string` | 스키마에 없는 새 응답 필드를 JSON으로 보존 |

읽기:

```python
from xrypto.storage import ParquetStore

store = ParquetStore("data")
table = store.read_range("KRW-BTC", 1)   # ts 오름차순, Decimal 그대로
```

거래가 없는 구간은 캔들이 아예 없다. 연속 인덱스를 가정하는 지표는 직접
reindex해서 결측을 명시적으로 처리해야 한다.

## 백테스트 시 반영할 것

`xrypto.market_rules`에 KRW 마켓 규칙이 있다.

```python
from decimal import Decimal
from xrypto.market_rules import align_price, round_trip_cost, is_orderable

align_price(Decimal("50000123.45"))      # 호가 단위로 정렬 → Decimal('50000000')
round_trip_cost(Decimal("1000000"))      # 왕복 수수료 0.1% → Decimal('1000')
is_orderable(Decimal("4999"))            # 최소 주문 금액 5,000원 → False
```

## 자동 수집

`.github/workflows/collect.yml`이 `10 * * * *`(매시 10분)에 돈다. cron은 정시에
정확히 돌지 않으므로(수 분~수십 분 지연, 드물게 스킵) 수집기는 시각이 아니라
저장된 마지막 봉을 기준으로 동작한다.

수동 실행:

```bash
gh workflow run collect.yml -f unit=1 -f limit=10
gh run watch
```

> 60일간 저장소 활동이 없으면 GitHub이 스케줄을 자동 비활성화한다. 봇 커밋만으로는
> 활동으로 인정되지 않을 수 있으니 주기적으로 Actions 탭을 확인할 것.

## 테스트

```bash
uv run pytest
uv run ruff check
```

테스트는 네트워크를 타지 않는다. 가짜 업비트 세션으로 429 백오프, 418 즉시 중단,
리전 폴백, 증분 복구, 미완성 캔들 제외를 검증한다.
