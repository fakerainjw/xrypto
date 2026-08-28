"""업비트 KRW 마켓 거래 규칙.

백테스트가 실거래와 덜 벌어지도록, 최소한 수수료·최소 주문 금액·호가 단위는
반영한다. (그래도 슬리피지와 체결 지연 때문에 실거래는 백테스트보다 나쁘다.)
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal

#: KRW 마켓 기본 수수료 (편도). 왕복이면 0.1%.
FEE_RATE = Decimal("0.0005")

#: 최소 주문 금액 (KRW).
MIN_ORDER_KRW = Decimal("5000")

#: 가격대별 호가 단위 (하한가, 틱 사이즈). 내림차순으로 평가한다.
#: 업비트가 밴드를 조정하는 경우가 있으므로 공식 표와 주기적으로 대조할 것.
TICK_SIZES: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("2000000"), Decimal("1000")),
    (Decimal("1000000"), Decimal("1000")),
    (Decimal("500000"), Decimal("500")),
    (Decimal("100000"), Decimal("100")),
    (Decimal("10000"), Decimal("10")),
    (Decimal("1000"), Decimal("1")),
    (Decimal("100"), Decimal("1")),
    (Decimal("10"), Decimal("0.1")),
    (Decimal("1"), Decimal("0.01")),
    (Decimal("0.1"), Decimal("0.001")),
    (Decimal("0.01"), Decimal("0.0001")),
    (Decimal("0.001"), Decimal("0.00001")),
    (Decimal("0.0001"), Decimal("0.000001")),
    (Decimal("0"), Decimal("0.00000001")),
)


def tick_size(price: Decimal) -> Decimal:
    """해당 가격대의 호가 단위."""
    price = Decimal(price)
    for lower, tick in TICK_SIZES:
        if price >= lower:
            return tick
    return TICK_SIZES[-1][1]


def align_price(price: Decimal, *, rounding: str = ROUND_HALF_EVEN) -> Decimal:
    """호가 단위에 맞춰 가격을 정렬. 임의 소수점 가격 체결은 가정하지 않는다."""
    price = Decimal(price)
    tick = tick_size(price)
    aligned = (price / tick).quantize(Decimal(1), rounding=rounding) * tick
    return aligned.quantize(tick)


def fee(notional: Decimal, rate: Decimal = FEE_RATE) -> Decimal:
    """주문 금액에 대한 수수료 (원 단위 내림)."""
    return (Decimal(notional) * rate).quantize(Decimal("1"), rounding=ROUND_DOWN)


def is_orderable(notional: Decimal) -> bool:
    """최소 주문 금액을 넘는지."""
    return Decimal(notional) >= MIN_ORDER_KRW


def round_trip_cost(notional: Decimal, rate: Decimal = FEE_RATE) -> Decimal:
    """왕복 수수료 (매수 + 매도). 단타 전략에서는 이게 결정적이다."""
    return fee(notional, rate) * 2
