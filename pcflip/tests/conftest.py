import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pcflip.config import Economics  # noqa: E402
from pcflip.watchlist import Watchlist  # noqa: E402
from pcflip.pricing.resolver import PriceResolver  # noqa: E402


@pytest.fixture(scope="session")
def watchlist() -> Watchlist:
    return Watchlist.load(ROOT / "watchlist.yaml")


@pytest.fixture(scope="session")
def resolver(watchlist) -> PriceResolver:
    return PriceResolver(None, watchlist, ROOT / "reference_prices.yaml")


@pytest.fixture(scope="session")
def econ() -> Economics:
    return Economics()
