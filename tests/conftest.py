from __future__ import annotations

import pytest

from tests.fakes import FakeMarket, FakeResponse, FakeSession

__all__ = ["FakeMarket", "FakeResponse", "FakeSession"]


@pytest.fixture
def fake_market():
    return FakeMarket
