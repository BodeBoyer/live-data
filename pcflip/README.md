# pcflip

Finds underpriced PC parts and gaming PCs on local marketplaces, values them against the
current used market, and tells you what to pay. Everything except the buying is automated.

It lives in this repo for convenience but is independent of the live-data refresh; move the
`pcflip/` folder to its own repo any time.

```
Facebook Marketplace ──┐                      ┌─ Discord / Telegram / ntfy / console alert
(Playwright or Apify)  ├─> extract components ─┤   "STRONG_BUY  ask $900, exit $1,578, max buy $1,184, open at $900"
pasted text ───────────┘   + red flags         └─ SQLite log + report.html
                               │
                      reference prices  <─  Terapeak (manual, best) > eBay Browse API (nightly) > seeds
```

## Why this works right now (Nov 2026)

DRAM and NAND are in a shortage: 32GB DDR5 kits retail for $430–600 (vs $100–200 a year ago),
SSD prices are up ~300%, and used 24GB GPUs (3090/4090) trade at $1,050–2,700. Most sellers,
especially of prebuilts, still price off what they paid. A 2023 gaming PC listed at $700 often
contains $500+ of RAM and SSD before you count the GPU. pcflip spots that gap automatically.

## 10-minute setup (works with zero API keys)

```bash
cd pcflip
pip install -e ".[all]"          # or ".[facebook]" / ".[llm]" individually
pcflip init                      # writes config.toml + .env from the examples
pcflip evaluate -p 900 "Gaming PC RTX 4070 Super 7800X3D 32GB DDR5 2TB NVMe"
```

```
[STRONG_BUY] Gaming PC RTX 4070 Super 7800X3D 32GB DDR5 2TB NVMe
  ask $900 | exit $1,578 via part_out
  max buy $1,184 | target profit $395 | spread $678 (+75%)
  open at $900
  - NVIDIA RTX 4070 Super 12GB: $800 (seed, low)
  ...
  > low-confidence prices (seed/interpolated): verify eBay sold comps before paying
```

Paste any listing (`pcflip evaluate -` reads stdin) and you get the verdict, the exit plan,
the max you can pay, and an opening offer. That alone replaces the spreadsheet.

## Turning on the automation

Each block is optional and independent. Secrets go in `pcflip/.env` (gitignored).

### 1. Live reference prices (eBay Browse API, free)

1. https://developer.ebay.com → My Account → Application Keys → create a **Production** keyset.
2. Put `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` in `.env`, then `pcflip prices`.
3. For hands-off nightly refreshes, add the same two values as **repository secrets**; the
   workflow `.github/workflows/pcflip-prices.yml` runs daily and commits
   `pcflip/data/prices.json` and `pcflip/data/PRICES.md`.

The public API only exposes *active* listings, so pcflip uses the lower quartile of filtered
asks × `active_to_sold_factor` (0.92) as the sold estimate. For the real sold median, spend
10 minutes a week in Terapeak (eBay Seller Hub → Research, free with a seller account) and
fill `reference_prices.yaml`. Manual entries newer than 21 days override everything.

### 2. Facebook Marketplace monitoring

Facebook has no API and its terms prohibit scraping. pcflip drives a real browser with your
own login, polls slowly, and only looks at searches you configure. Use a dedicated account
if you can, keep `poll_interval_seconds` ≥ 300, and treat a ban as a cost of doing business.

```bash
pcflip fb-login                  # browser opens; log in once; session saved to data/fb_state.json
# edit config.toml: [facebook].location = your city slug from a Marketplace URL, radius, [[searches]]
pcflip scan --debug              # one pass; --debug dumps raw payloads to data/debug if parsing fails
pcflip watch                     # loop forever; alerts on new BUY/WATCH listings
```

Hosted alternative: set `[apify] enabled = true` plus `APIFY_TOKEN` / `APIFY_ACTOR_ID` in `.env`
and `pcflip scan --source apify` runs an Apify Marketplace actor instead of a local browser.

Run `pcflip watch` on any always-on machine (old laptop, Raspberry Pi, a $5 VPS). Example
systemd unit and cron line are at the bottom of this file.

### 3. Alerts

Set one or more of `DISCORD_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN`+`TELEGRAM_CHAT_ID`, `NTFY_TOPIC`
in `.env`, list the channels under `[alerts]` in `config.toml`, then `pcflip test-alerts`.
`min_verdict` controls the noise floor; price drops ≥ `realert_on_price_drop_pct` re-alert.

### 4. Claude for messy prebuilt listings (optional)

Regexes handle "RTX 4070 Super, 7800X3D, 32GB DDR5" fine. For prose like "has the forty-seventy
ti super and thirty-two gigs of memory", set `[llm] enabled = true` and `ANTHROPIC_API_KEY`.
The model returns a strict JSON spec list; every model name is re-validated against the
watchlist, so it cannot invent a SKU. Results are cached. It only runs when the regex pass
left gaps on a prebuilt-looking listing, so cost is a few cents a day.

## The valuation rule

```
single part:   exit = max( sold_est × 0.95 [local],  sold_est × (1 − 13.6%) − $0.40 − shipping [eBay] )
prebuilt:      parts_sum = Σ sold_est(components) + other-parts allowance ($150 DDR5-era / $100 DDR4)
               exit = max( parts_sum × 0.78 [sell whole, locally],  Σ eBay net(components) + allowance/2 [part out] )
target_profit  = max($100, 25% × exit)
max_buy        = exit − target_profit
verdict        = STRONG_BUY (ask ≤ 90% of max_buy) | BUY (≤ max_buy) | WATCH (≤ 115%) | PASS
offer          = min(ask, 90% of max_buy), rounded down to $5
```

Hard red flags ("for parts", "no display", "won't boot", "artifacting"...) force PASS but still
show the working-unit value. Soft flags adjust: mining history −15%, untested/as-is −8%.
Conservative defaults everywhere: unknown RAM type → DDR4, unknown SSD interface → SATA.
All constants live in `[economics]` in `config.toml`.

## Commands

| command | what it does |
|---|---|
| `pcflip evaluate [-p PRICE] TEXT` | value one listing (TEXT may be `-` for stdin) |
| `pcflip prices [--only SKU...]` | refresh eBay comps, write `data/prices.json`, `PRICES.md`, `report.html` |
| `pcflip fb-login` | one-time Facebook login, saves session |
| `pcflip scan` / `pcflip watch` | run configured searches once / forever, alert on deals |
| `pcflip report` | regenerate `data/report.html` (valuations, prices, deals) |
| `pcflip deals add --cost 600 --title "..."` | log a purchase (your COGS record) |
| `pcflip deals sold ID --price 1000 --platform local` | log the sale, see profit/ROI |
| `pcflip deals export` | CSV for taxes |
| `pcflip skus` | every SKU with its current reference price and source |
| `pcflip test-alerts` | confirm channel wiring |

## Files

```
pcflip/
  watchlist.yaml          81 SKUs: recognizers, eBay queries/filters, seed prices
  reference_prices.yaml   your Terapeak sold medians (highest priority source)
  config.example.toml     economics, searches, channels  -> copy to config.toml
  .env.example            secrets                         -> copy to .env
  pcflip/
    valuation/extract.py  regex component + red-flag extraction (conservative)
    valuation/value.py    the math above
    valuation/llm.py      optional Claude structured extraction
    pricing/ebay.py       Browse API feed; pricing/resolver.py picks the best source
    listings/facebook.py  Playwright scraper (GraphQL interception + DOM fallback)
    listings/apify.py     hosted scraper; listings/manual.py pasted text
    alerts/dispatch.py    Discord / Telegram / ntfy / console
    pipeline.py           store -> value -> alert, with dedupe
    report.py, deals.py, cli.py
  data/                   flip.db, prices.json, PRICES.md, report.html (DB and session are gitignored)
  tests/                  64 tests; `python -m pytest`
```

## Seed prices are estimates

The seeds in `watchlist.yaml` were set 2026-11-09 from public price trackers and are marked
`confidence: low`. Every alert says which source priced each part. Before your first purchase,
either run `pcflip prices` with eBay keys or put a few Terapeak numbers in
`reference_prices.yaml` for the SKUs you are actually chasing.

## Operating notes

- **Speed wins.** Good listings go in under an hour. Keep the message template short: ask for
  a Task Manager / Speccy screenshot, offer same-day cash pickup.
- **Test before paying.** USB with Windows installer, CrystalDiskInfo (SSD health), HWiNFO
  (RAM speed/capacity), FurMark or Heaven 10 min (GPU), memtest86. Walk from artifacts, hot VRAM, SSDs past 80% wear.
- **Stolen goods.** Ask for proof of purchase above $500, meet in public, pay cash or Zelle at pickup.
- **Taxes.** Federal 1099-K threshold for 2026 is $20,000 and 200 transactions, several states are
  lower, and the income is taxable regardless. `pcflip deals` is your ledger from day one.
- **Don't speculate.** Memory is up 4–5x; flip in days, not months.

## Running `watch` as a service (Linux)

```ini
# /etc/systemd/system/pcflip.service
[Unit]
Description=pcflip marketplace watcher
After=network-online.target
[Service]
WorkingDirectory=/home/you/live-data/pcflip
ExecStart=/home/you/.local/bin/pcflip watch
Restart=always
RestartSec=60
[Install]
WantedBy=multi-user.target
```

Or cron for the price refresh on a box of your own: `17 4 * * * cd ~/live-data/pcflip && pcflip prices >> data/prices.log 2>&1`
