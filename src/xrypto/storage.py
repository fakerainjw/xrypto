"""월별 parquet 저장소.

레이아웃: ``data/{MARKET}/{UNIT}m/YYYY-MM.parquet`` (월 경계는 UTC 기준).

* 가격·금액은 float이 아니라 ``decimal128``로 저장한다. 읽으면 다시 ``Decimal``이
  나오므로 계산 경로 어디에서도 float이 끼어들지 않는다.
* 저장 시 ``ts`` 기준으로 중복을 제거한다 (역방향 페이지 경계에서 겹친다).
* 업비트 응답 필드는 그대로 보존하고, 스키마에 없는 새 필드는 ``extra`` 컬럼에
  JSON으로 남겨 유실되지 않게 한다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from xrypto.timeutil import as_utc, month_key, parse_upbit_utc

#: 소수 18자리면 업비트의 가격/수량/거래대금을 모두 손실 없이 담는다.
DECIMAL_SCALE = 18
DECIMAL_TYPE = pa.decimal128(38, DECIMAL_SCALE)
_QUANT = Decimal(1).scaleb(-DECIMAL_SCALE)

DECIMAL_FIELDS = (
    "opening_price",
    "high_price",
    "low_price",
    "trade_price",
    "candle_acc_trade_price",
    "candle_acc_trade_volume",
)

SCHEMA = pa.schema(
    [
        pa.field("ts", pa.timestamp("us", tz="UTC")),
        pa.field("market", pa.string()),
        pa.field("candle_date_time_utc", pa.string()),
        pa.field("candle_date_time_kst", pa.string()),
        pa.field("opening_price", DECIMAL_TYPE),
        pa.field("high_price", DECIMAL_TYPE),
        pa.field("low_price", DECIMAL_TYPE),
        pa.field("trade_price", DECIMAL_TYPE),
        pa.field("timestamp", pa.int64()),
        pa.field("candle_acc_trade_price", DECIMAL_TYPE),
        pa.field("candle_acc_trade_volume", DECIMAL_TYPE),
        pa.field("unit", pa.int32()),
        pa.field("extra", pa.string()),
    ]
)

_KNOWN_KEYS = {field.name for field in SCHEMA} - {"ts", "extra"}


def to_decimal(value: Any) -> Decimal | None:
    """숫자를 ``Decimal``로. float이 들어와도 문자열을 거쳐 오차를 막는다."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        dec = value
    else:
        dec = Decimal(str(value))
    exponent = dec.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > DECIMAL_SCALE:
        dec = dec.quantize(_QUANT, rounding=ROUND_HALF_EVEN)
    return dec


def normalize_candle(raw: dict[str, Any]) -> dict[str, Any]:
    """업비트 캔들 응답 한 건을 저장 스키마 형태로 변환."""
    ts = parse_upbit_utc(raw["candle_date_time_utc"])
    extra = {key: value for key, value in raw.items() if key not in _KNOWN_KEYS}
    row: dict[str, Any] = {
        "ts": ts,
        "market": raw["market"],
        "candle_date_time_utc": raw["candle_date_time_utc"],
        "candle_date_time_kst": raw.get("candle_date_time_kst"),
        "timestamp": int(raw["timestamp"]) if raw.get("timestamp") is not None else None,
        "unit": int(raw["unit"]) if raw.get("unit") is not None else None,
        "extra": json.dumps(extra, default=str, sort_keys=True) if extra else None,
    }
    for field in DECIMAL_FIELDS:
        row[field] = to_decimal(raw.get(field))
    return row


def rows_to_table(rows: Sequence[dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(
        [{f.name: row.get(f.name) for f in SCHEMA} for row in rows], schema=SCHEMA
    )


class ParquetStore:
    """``data/{MARKET}/{UNIT}m/YYYY-MM.parquet`` 저장소."""

    def __init__(self, root: str | Path = "data", compression: str = "zstd") -> None:
        self.root = Path(root)
        self.compression = compression

    # ------------------------------------------------------------------ #
    def dir_for(self, market: str, unit: int) -> Path:
        return self.root / market / f"{unit}m"

    def path_for(self, market: str, unit: int, when: datetime) -> Path:
        return self.dir_for(market, unit) / f"{month_key(when)}.parquet"

    def month_files(self, market: str, unit: int) -> list[Path]:
        directory = self.dir_for(market, unit)
        if not directory.is_dir():
            return []
        return sorted(directory.glob("*.parquet"))

    # ------------------------------------------------------------------ #
    def last_ts(self, market: str, unit: int) -> datetime | None:
        """마지막으로 저장된 봉의 시작 시각. 증분 수집의 기준점."""
        for path in reversed(self.month_files(market, unit)):
            table = pq.read_table(path, columns=["ts"])
            if table.num_rows == 0:
                continue
            values = [as_utc(value) for value in table.column("ts").to_pylist() if value]
            if values:
                return max(values)
        return None

    def read_month(self, market: str, unit: int, when: datetime) -> pa.Table:
        path = self.path_for(market, unit, when)
        if not path.exists():
            return SCHEMA.empty_table()
        return pq.read_table(path, schema=SCHEMA)

    def read_range(
        self,
        market: str,
        unit: int,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pa.Table:
        """저장된 구간을 ts 오름차순으로 읽는다 (start/end 모두 inclusive)."""
        tables = [pq.read_table(path, schema=SCHEMA) for path in self.month_files(market, unit)]
        if not tables:
            return SCHEMA.empty_table()
        rows = pa.concat_tables(tables).to_pylist()
        start_utc = as_utc(start) if start else None
        end_utc = as_utc(end) if end else None
        kept = [
            row
            for row in rows
            if (start_utc is None or as_utc(row["ts"]) >= start_utc)
            and (end_utc is None or as_utc(row["ts"]) <= end_utc)
        ]
        kept.sort(key=lambda row: as_utc(row["ts"]))
        return rows_to_table(kept)

    # ------------------------------------------------------------------ #
    def write_candles(self, market: str, unit: int, rows: Iterable[dict[str, Any]]) -> int:
        """정규화된 행들을 월별 파일에 병합 저장. 새로 늘어난 행 수를 돌려준다."""
        by_month: dict[str, dict[datetime, dict[str, Any]]] = {}
        for row in rows:
            ts = as_utc(row["ts"])
            row = {**row, "ts": ts}
            by_month.setdefault(month_key(ts), {})[ts] = row

        added = 0
        for new_rows in by_month.values():
            any_ts = next(iter(new_rows))
            path = self.path_for(market, unit, any_ts)
            merged: dict[datetime, dict[str, Any]] = {}
            if path.exists():
                for existing in pq.read_table(path, schema=SCHEMA).to_pylist():
                    merged[as_utc(existing["ts"])] = existing
            before = len(merged)
            # 새 데이터가 이긴다 (같은 ts면 나중에 받은 값으로 덮어쓴다).
            merged.update(new_rows)
            added += len(merged) - before

            ordered = [merged[ts] for ts in sorted(merged)]
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".parquet.tmp")
            pq.write_table(rows_to_table(ordered), tmp, compression=self.compression)
            tmp.replace(path)
        return added
