"""시각 처리 유틸.

내부적으로 시각은 **항상 tz-aware UTC**로 다룬다. KST는 표시할 때만 쓴다.
업비트 응답의 `candle_date_time_utc` / `candle_date_time_kst`는 둘 다
타임존 표기가 없는 naive 문자열이라, 섞이면 9시간이 조용히 어긋난다.
파싱은 반드시 이 모듈의 함수를 거친다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), name="KST")


def parse_upbit_utc(value: str) -> datetime:
    """`candle_date_time_utc`(naive 문자열)를 tz-aware UTC로 변환."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        return dt.astimezone(UTC)
    return dt.replace(tzinfo=UTC)


def parse_upbit_kst(value: str) -> datetime:
    """`candle_date_time_kst`(naive 문자열)를 tz-aware UTC로 변환."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        return dt.astimezone(UTC)
    return dt.replace(tzinfo=KST).astimezone(UTC)


def to_upbit_param(dt: datetime) -> str:
    """업비트 `to` 파라미터용 UTC 문자열(`2024-01-01T00:00:00Z`)."""
    return as_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def as_utc(dt: datetime) -> datetime:
    """naive면 UTC로 간주하고, aware면 UTC로 변환."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_kst(dt: datetime) -> datetime:
    """표시용 KST 변환."""
    return as_utc(dt).astimezone(KST)


def now_utc() -> datetime:
    return datetime.now(UTC)


def floor_to_unit(dt: datetime, unit_minutes: int) -> datetime:
    """`unit_minutes` 분봉 경계로 내림. 봉이 시작하는 시각을 돌려준다."""
    if unit_minutes <= 0:
        raise ValueError("unit_minutes must be positive")
    dt = as_utc(dt).replace(second=0, microsecond=0)
    epoch_minutes = int(dt.timestamp() // 60)
    return datetime.fromtimestamp((epoch_minutes // unit_minutes) * unit_minutes * 60, tz=UTC)


def last_closed_candle_start(now: datetime, unit_minutes: int) -> datetime:
    """`now` 시점에서 마감이 끝난 마지막 봉의 시작 시각.

    봉 ``T``는 ``[T, T+unit)`` 구간을 덮으므로 ``floor(now)`` 봉은 아직 열려 있다.
    미완성 캔들은 값이 계속 변해 백테스트를 오염시키므로 저장 대상에서 제외한다.
    """
    return floor_to_unit(now, unit_minutes) - timedelta(minutes=unit_minutes)


def is_closed(candle_start: datetime, unit_minutes: int, now: datetime) -> bool:
    """봉이 마감됐는지 여부."""
    return as_utc(candle_start) + timedelta(minutes=unit_minutes) <= as_utc(now)


def month_key(dt: datetime) -> str:
    """월별 파일 이름에 쓰는 `YYYY-MM` 키 (UTC 기준)."""
    return as_utc(dt).strftime("%Y-%m")
