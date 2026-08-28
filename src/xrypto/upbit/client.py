"""업비트 Quotation API 클라이언트.

주의사항 (CLAUDE.md 참조):

* 시세 조회는 API 키가 필요 없다.
* **Origin 헤더를 절대 붙이지 않는다.** 붙이면 시세 조회가 10초당 1회로 제한된다.
* Rate limit은 초 단위. 응답 ``Remaining-Req`` 헤더에서 ``sec`` 값만 참조한다
  (``min``은 deprecated된 고정값이라 믿으면 안 된다).
* 429는 지수 백오프로 물러난다. 429에도 계속 요청하면 418과 함께 차단되고
  반복할수록 차단 시간이 길어지므로, **418이면 즉시 중단**한다.
* Base URL이 리전별로 다르다. GitHub Actions 러너는 해외 IP이므로 시작 시
  접근 가능한 엔드포인트를 판별해 폴백한다.
"""

from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime
from decimal import Decimal
from typing import Any

import requests

from xrypto.timeutil import to_upbit_param

log = logging.getLogger(__name__)

#: 국내 / 글로벌 엔드포인트. 앞에서부터 접근 가능한 것을 고른다.
QUOTATION_HOSTS: tuple[str, ...] = ("https://api.upbit.com", "https://sg-api.upbit.com")

#: 캔들 조회는 호출당 최대 200개.
MAX_CANDLE_COUNT = 200

#: 초당 10회 이하로 여유 있게. 호출 사이 최소 간격(초).
MIN_INTERVAL_SEC = 0.15


class UpbitError(RuntimeError):
    """업비트 API 호출 실패."""


class UpbitBlockedError(UpbitError):
    """418 — IP/계정이 차단됨. 재시도하면 차단 시간만 길어지므로 즉시 중단한다."""


class UpbitClient:
    """Quotation API 전용 클라이언트 (API 키 불필요)."""

    def __init__(
        self,
        hosts: tuple[str, ...] | list[str] = QUOTATION_HOSTS,
        *,
        session: requests.Session | None = None,
        min_interval: float = MIN_INTERVAL_SEC,
        max_retries: int = 5,
        timeout: float = 10.0,
        sleep: Any = time.sleep,
    ) -> None:
        self.hosts = tuple(hosts)
        if not self.hosts:
            raise ValueError("at least one host is required")
        self.session = session or requests.Session()
        # Origin 헤더는 절대 붙이지 않는다.
        self.session.headers.update({"Accept": "application/json"})
        self.session.headers.pop("Origin", None)
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self._sleep = sleep
        self._base_url: str | None = None
        self._last_call = 0.0

    # ------------------------------------------------------------------ #
    # 엔드포인트 판별
    # ------------------------------------------------------------------ #
    @property
    def base_url(self) -> str:
        """접근 가능한 base URL. 최초 호출 시 후보를 순서대로 판별한다."""
        if self._base_url is None:
            self._base_url = self._resolve_base_url()
        return self._base_url

    def _resolve_base_url(self) -> str:
        last_error: Exception | None = None
        for host in self.hosts:
            try:
                res = self.session.get(
                    f"{host}/v1/market/all",
                    params={"isDetails": "false"},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:  # 네트워크 자체가 막힌 리전
                log.warning("endpoint %s unreachable: %s", host, exc)
                last_error = exc
                continue
            if res.status_code == 418:
                raise UpbitBlockedError(f"418 from {host}: blocked, stopping immediately")
            if res.ok:
                log.info("using upbit endpoint %s", host)
                return host
            log.warning("endpoint %s returned HTTP %s", host, res.status_code)
            last_error = UpbitError(f"{host} returned HTTP {res.status_code}")
        raise UpbitError(f"no reachable upbit endpoint among {self.hosts}: {last_error}")

    # ------------------------------------------------------------------ #
    # 저수준 요청
    # ------------------------------------------------------------------ #
    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            self._sleep(self.min_interval - elapsed)

    @staticmethod
    def _remaining_sec(headers: Any) -> int | None:
        """``Remaining-Req: group=default; min=1799; sec=29``에서 sec만 읽는다."""
        raw = headers.get("Remaining-Req") if headers else None
        if not raw:
            return None
        for part in str(raw).split(";"):
            key, _, value = part.strip().partition("=")
            if key.strip() == "sec":
                try:
                    return int(value)
                except ValueError:
                    return None
        return None

    def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        backoff = 1.0
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            self._throttle()
            try:
                res = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                log.warning("request error on %s (attempt %d): %s", path, attempt + 1, exc)
                self._sleep(backoff + random.uniform(0, 0.3))
                backoff *= 2
                continue
            finally:
                self._last_call = time.monotonic()

            if res.status_code == 418:
                # 재시도하면 차단 시간이 길어질 뿐이다.
                raise UpbitBlockedError(f"418 on {path}: blocked, stopping immediately")

            if res.status_code == 429:
                wait = backoff + random.uniform(0, 0.3)
                log.warning("429 on %s, backing off %.1fs (attempt %d)", path, wait, attempt + 1)
                self._sleep(wait)
                backoff *= 2
                last_error = UpbitError(f"429 on {path}")
                continue

            if res.status_code >= 500:
                last_error = UpbitError(f"HTTP {res.status_code} on {path}")
                self._sleep(backoff + random.uniform(0, 0.3))
                backoff *= 2
                continue

            if not res.ok:
                raise UpbitError(f"HTTP {res.status_code} on {path}: {res.text[:200]}")

            remaining = self._remaining_sec(res.headers)
            if remaining is not None and remaining <= 1:
                # 초 단위 창이 거의 찼다. 다음 창까지 양보한다.
                self._sleep(1.0)

            # 가격·금액을 float으로 받으면 그 시점에 정밀도가 깨진다.
            # 원본 문자열을 Decimal로 직접 파싱한다.
            return json.loads(res.text, parse_float=Decimal)

        raise UpbitError(f"giving up on {path} after {self.max_retries} attempts: {last_error}")

    # ------------------------------------------------------------------ #
    # 공개 API
    # ------------------------------------------------------------------ #
    def get_markets(self, quote: str | None = "KRW") -> list[str]:
        """마켓 코드 목록. 심볼은 하드코딩하지 않고 항상 이 API로 받아온다."""
        payload = self._request("/v1/market/all", {"isDetails": "false"})
        codes = [item["market"] for item in payload]
        if quote:
            prefix = f"{quote.upper()}-"
            codes = [code for code in codes if code.startswith(prefix)]
        return sorted(codes)

    def get_candles(
        self,
        market: str,
        unit: int,
        *,
        to: datetime | None = None,
        count: int = MAX_CANDLE_COUNT,
    ) -> list[dict[str, Any]]:
        """분봉 조회.

        ``since``는 없고 ``to``(마지막 캔들 시각, exclusive) 기준 역방향
        페이지네이션만 가능하다. 응답은 최신 → 과거 순으로 온다.
        """
        if count > MAX_CANDLE_COUNT:
            raise ValueError(f"count must be <= {MAX_CANDLE_COUNT}")
        params: dict[str, Any] = {"market": market, "count": count}
        if to is not None:
            params["to"] = to_upbit_param(to)
        return self._request(f"/v1/candles/minutes/{unit}", params)
