"""Configuration: config.toml (non-secret settings) + environment variables (secrets).

Resolution order for every secret: environment variable, then a `.env` file next to
the config, then nothing (feature disabled). Nothing in this module makes network calls.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # .../pcflip (project dir)
DEFAULT_CONFIG_PATH = PACKAGE_ROOT / "config.toml"
EXAMPLE_CONFIG_PATH = PACKAGE_ROOT / "config.example.toml"
DEFAULT_WATCHLIST_PATH = PACKAGE_ROOT / "watchlist.yaml"
DEFAULT_REFERENCE_PATH = PACKAGE_ROOT / "reference_prices.yaml"
DEFAULT_DATA_DIR = PACKAGE_ROOT / "data"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, '#' comments, no interpolation.
    Never overrides variables already present in the environment."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        if key and key not in os.environ and value:
            os.environ[key] = value


@dataclass
class Economics:
    ebay_fee_rate: float = 0.136          # final value fee on item + shipping, most categories
    ebay_per_order_fee: float = 0.40
    local_sale_factor: float = 0.95       # what a local buyer pays vs. eBay sold median for a single part
    prebuilt_whole_factor: float = 0.78   # whole-PC local price as a share of the parts-sum
    prebuilt_other_parts_ddr5: float = 150.0  # motherboard + PSU + case + cooler allowance when not itemized
    prebuilt_other_parts_ddr4: float = 100.0
    target_profit_floor: float = 100.0    # never alert on less than this expected profit
    target_margin: float = 0.25           # ...or less than this share of the exit price
    strong_buy_ratio: float = 0.90        # ask <= max_buy * this  -> STRONG_BUY
    watch_ratio: float = 1.15             # ask <= max_buy * this  -> WATCH (negotiable)
    active_to_sold_factor: float = 0.92   # eBay active p25 -> estimated sold median
    shipping_estimate: dict[str, float] = field(default_factory=lambda: {
        "gpu": 15.0, "cpu": 6.0, "ram": 6.0, "ssd": 6.0, "prebuilt": 60.0, "other": 12.0,
    })


@dataclass
class SearchSpec:
    query: str
    location: str = ""          # Facebook location slug, e.g. "austin" or "108424279189115"
    radius_km: int = 40
    min_price: int | None = None
    max_price: int | None = None


@dataclass
class Settings:
    config_path: Path
    data_dir: Path
    watchlist_path: Path
    reference_path: Path
    economics: Economics
    searches: list[SearchSpec]
    facebook: dict[str, Any]
    apify: dict[str, Any]
    alerts: dict[str, Any]
    llm: dict[str, Any]
    ebay: dict[str, Any]
    poll_interval_seconds: int = 300

    # ---- derived helpers ---------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.data_dir / "flip.db"

    @property
    def fb_state_path(self) -> Path:
        return Path(self.facebook.get("state_path") or (self.data_dir / "fb_state.json"))

    def env(self, name: str, default: str | None = None) -> str | None:
        value = os.environ.get(name)
        return value if value else default


def load_settings(config_path: str | Path | None = None) -> Settings:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    _load_dotenv(path.parent / ".env")
    raw: dict[str, Any] = {}
    source = path if path.exists() else EXAMPLE_CONFIG_PATH
    if source.exists():
        raw = tomllib.loads(source.read_text(encoding="utf-8"))

    econ_raw = dict(raw.get("economics", {}))
    shipping = dict(Economics().shipping_estimate)
    shipping.update(econ_raw.pop("shipping_estimate", {}) or {})
    economics = Economics(**{k: v for k, v in econ_raw.items() if k in Economics.__dataclass_fields__}, shipping_estimate=shipping)

    searches = [
        SearchSpec(
            query=str(s["query"]),
            location=str(s.get("location", raw.get("facebook", {}).get("location", ""))),
            radius_km=int(s.get("radius_km", raw.get("facebook", {}).get("radius_km", 40))),
            min_price=s.get("min_price"),
            max_price=s.get("max_price"),
        )
        for s in raw.get("searches", [])
        if s.get("query")
    ]

    data_dir = Path(raw.get("data_dir") or DEFAULT_DATA_DIR)
    if not data_dir.is_absolute():
        data_dir = path.parent / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        config_path=path,
        data_dir=data_dir,
        watchlist_path=Path(raw.get("watchlist") or DEFAULT_WATCHLIST_PATH),
        reference_path=Path(raw.get("reference_prices") or DEFAULT_REFERENCE_PATH),
        economics=economics,
        searches=searches,
        facebook=dict(raw.get("facebook", {})),
        apify=dict(raw.get("apify", {})),
        alerts=dict(raw.get("alerts", {})),
        llm=dict(raw.get("llm", {})),
        ebay=dict(raw.get("ebay", {})),
        poll_interval_seconds=int(raw.get("poll_interval_seconds", 300)),
    )
