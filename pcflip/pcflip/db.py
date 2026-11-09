"""SQLite persistence. One file, a handful of tables, no ORM."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    sku            TEXT NOT NULL,
    source         TEXT NOT NULL,          -- 'ebay_active' | 'manual_sold' | 'seed'
    observed_at    TEXT NOT NULL,          -- ISO8601 UTC
    median         REAL,
    p25            REAL,
    p75            REAL,
    low            REAL,
    high           REAL,
    sample_size    INTEGER,
    est_sold       REAL,                   -- what we treat as the used sold median
    detail         TEXT,                   -- JSON blob (query, filters, sample titles)
    PRIMARY KEY (sku, source, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_prices_sku ON prices (sku, observed_at DESC);

CREATE TABLE IF NOT EXISTS listings (
    id             TEXT PRIMARY KEY,       -- '<source>:<listing id>'
    source         TEXT NOT NULL,
    listing_id     TEXT NOT NULL,
    url            TEXT,
    title          TEXT,
    description    TEXT,
    price          REAL,
    location       TEXT,
    image_url      TEXT,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL,
    last_price     REAL,
    raw            TEXT                    -- JSON
);

CREATE TABLE IF NOT EXISTS valuations (
    listing_id     TEXT NOT NULL,
    valued_at      TEXT NOT NULL,
    ask            REAL,
    est_value      REAL,
    best_exit      REAL,
    max_buy        REAL,
    spread         REAL,
    spread_pct     REAL,
    verdict        TEXT,
    confidence     TEXT,
    detail         TEXT,                   -- JSON
    PRIMARY KEY (listing_id, valued_at)
);

CREATE TABLE IF NOT EXISTS alerts (
    listing_id     TEXT PRIMARY KEY,
    alerted_at     TEXT NOT NULL,
    price          REAL,
    verdict        TEXT,
    channels       TEXT
);

CREATE TABLE IF NOT EXISTS deals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT NOT NULL,
    listing_id     TEXT,
    title          TEXT,
    cost           REAL NOT NULL,
    extra_cost     REAL DEFAULT 0,         -- gas, thermal paste, parts replaced
    bought_at      TEXT,
    sold_at        TEXT,
    sale_price     REAL,
    sale_fees      REAL DEFAULT 0,
    sale_shipping  REAL DEFAULT 0,
    platform       TEXT,                   -- 'local' | 'ebay' | ...
    notes          TEXT,
    test_results   TEXT
);

CREATE TABLE IF NOT EXISTS llm_cache (
    key            TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    model          TEXT,
    response       TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ---- prices -------------------------------------------------------------
    def insert_price(self, sku: str, source: str, stats: dict[str, Any], est_sold: float | None,
                     detail: dict[str, Any] | None = None, observed_at: str | None = None) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO prices (sku, source, observed_at, median, p25, p75, low, high, sample_size, est_sold, detail)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (sku, source, observed_at or utcnow(), stats.get("median"), stats.get("p25"), stats.get("p75"),
                 stats.get("low"), stats.get("high"), stats.get("n"), est_sold, json.dumps(detail or {})),
            )

    def latest_prices(self, sku: str | None = None) -> list[sqlite3.Row]:
        """Most recent row per (sku, source)."""
        sql = (
            "SELECT p.* FROM prices p JOIN ("
            "  SELECT sku, source, MAX(observed_at) AS observed_at FROM prices GROUP BY sku, source"
            ") m ON p.sku = m.sku AND p.source = m.source AND p.observed_at = m.observed_at"
        )
        params: tuple = ()
        if sku:
            sql += " WHERE p.sku = ?"
            params = (sku,)
        return list(self.conn.execute(sql + " ORDER BY p.sku, p.source", params))

    def price_history(self, sku: str, limit: int = 60) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM prices WHERE sku = ? ORDER BY observed_at DESC LIMIT ?", (sku, limit)))

    # ---- listings -----------------------------------------------------------
    def upsert_listing(self, listing: dict[str, Any]) -> tuple[bool, float | None]:
        """Insert or refresh a listing. Returns (is_new, previous_price)."""
        now = utcnow()
        key = f"{listing['source']}:{listing['listing_id']}"
        row = self.conn.execute("SELECT price, last_price FROM listings WHERE id = ?", (key,)).fetchone()
        with self.tx() as c:
            if row is None:
                c.execute(
                    "INSERT INTO listings (id, source, listing_id, url, title, description, price, location, image_url,"
                    " first_seen, last_seen, last_price, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, listing["source"], str(listing["listing_id"]), listing.get("url"), listing.get("title"),
                     listing.get("description"), listing.get("price"), listing.get("location"), listing.get("image_url"),
                     now, now, listing.get("price"), json.dumps(listing.get("raw") or {}, default=str)),
                )
                return True, None
            prev = row["price"]
            c.execute(
                "UPDATE listings SET last_seen = ?, last_price = price, price = ?, title = COALESCE(?, title),"
                " description = COALESCE(?, description), url = COALESCE(?, url), image_url = COALESCE(?, image_url) WHERE id = ?",
                (now, listing.get("price"), listing.get("title"), listing.get("description"), listing.get("url"),
                 listing.get("image_url"), key),
            )
            return False, prev

    def get_listing(self, key: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM listings WHERE id = ?", (key,)).fetchone()

    def recent_listings(self, limit: int = 50) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM listings ORDER BY last_seen DESC LIMIT ?", (limit,)))

    # ---- valuations / alerts --------------------------------------------------
    def insert_valuation(self, listing_key: str, result: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO valuations (listing_id, valued_at, ask, est_value, best_exit, max_buy, spread,"
                " spread_pct, verdict, confidence, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (listing_key, utcnow(), result.get("ask"), result.get("est_value"), result.get("best_exit"),
                 result.get("max_buy"), result.get("spread"), result.get("spread_pct"), result.get("verdict"),
                 result.get("confidence"), json.dumps(result, default=str)),
            )

    def recent_valuations(self, limit: int = 50, min_verdicts: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
        sql = (
            "SELECT v.*, l.title, l.url, l.location, l.source FROM valuations v JOIN ("
            "  SELECT listing_id, MAX(valued_at) AS valued_at FROM valuations GROUP BY listing_id"
            ") m ON v.listing_id = m.listing_id AND v.valued_at = m.valued_at"
            " JOIN listings l ON l.id = v.listing_id"
        )
        params: list[Any] = []
        if min_verdicts:
            sql += " WHERE v.verdict IN (%s)" % ",".join("?" for _ in min_verdicts)
            params.extend(min_verdicts)
        sql += " ORDER BY v.valued_at DESC LIMIT ?"
        params.append(limit)
        return list(self.conn.execute(sql, params))

    def last_alert(self, listing_key: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM alerts WHERE listing_id = ?", (listing_key,)).fetchone()

    def record_alert(self, listing_key: str, price: float | None, verdict: str, channels: list[str]) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO alerts (listing_id, alerted_at, price, verdict, channels) VALUES (?,?,?,?,?)",
                (listing_key, utcnow(), price, verdict, ",".join(channels)),
            )

    # ---- deals ------------------------------------------------------------------
    def add_deal(self, **fields: Any) -> int:
        cols = ["created_at"] + list(fields.keys())
        vals = [utcnow()] + list(fields.values())
        with self.tx() as c:
            cur = c.execute(
                f"INSERT INTO deals ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", vals)
            return int(cur.lastrowid)

    def update_deal(self, deal_id: int, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE deals SET {sets} WHERE id = ?", (*fields.values(), deal_id))

    def deals(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM deals ORDER BY id"))

    # ---- llm cache ---------------------------------------------------------------
    def cache_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT response FROM llm_cache WHERE key = ?", (key,)).fetchone()
        return row["response"] if row else None

    def cache_put(self, key: str, model: str, response: str) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO llm_cache (key, created_at, model, response) VALUES (?,?,?,?)",
                      (key, utcnow(), model, response))
