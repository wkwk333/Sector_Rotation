# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A set of standalone Python scripts that track US equity sector/factor money-rotation
using yfinance ETF price data, and render the results as CSVs, per-pair charts, a
one-page dashboard, and a top-holdings/valuation snapshot. No test suite, no package
manager beyond pip, no web server — this is a personal analysis tool run from the CLI
or as a bundled .exe.

All user-facing text (CLI output, chart labels, docs) is in Japanese.

## Commands

```powershell
# one-time setup (venv already exists at .\venv if present)
py -3.11 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt

# run the pipeline (each step reads the previous step's output/ files)
.\venv\Scripts\python.exe sector_rotation_monitor.py   # 1. fetch prices, compute ratios -> output/*.csv
.\venv\Scripts\python.exe plot_rotation.py              # 2. per-pair charts -> output/01-05_*.png
.\venv\Scripts\python.exe generate_dashboard.py         # 3. one-page dashboard -> output/sector_rotation_dashboard_*.png
.\venv\Scripts\python.exe top_holdings.py               # 4. top-10 holdings + valuation -> output/06_*.png

# or run steps 1-3 in one process
.\venv\Scripts\python.exe run_pipeline.py

# rebuild the standalone exe (bundles run_pipeline.py, i.e. steps 1-3; top_holdings.py is NOT included)
.\venv\Scripts\python.exe -m pip install pyinstaller   # first time only
.\build_exe.ps1   # -> dist\SectorRotationDashboard.exe
```

There is no lint/test command — there is no test suite in this repo.

## Architecture

### Data flow
`sector_rotation_monitor.py` is the single source of truth for what gets tracked
(`CONFIG` dict at the top of the file) and the only script that hits the network for
price data. Every other script (`plot_rotation.py`, `generate_dashboard.py`,
`top_holdings.py`) imports `CONFIG` from it and reads the dated CSVs it already wrote
into `output/` — they never re-fetch prices themselves. This means step 1 must always
be run before 2-4, and changing `CONFIG` (adding/removing/renaming a pair) is the one
edit that ripples through the whole pipeline.

### The two indicator types in CONFIG
- **`CONFIG["pairs"]`**: ratio-based. Each pair has `inflow_tickers` / `outflow_tickers`
  (each side is equal-weighted into a basket normalized to 1.0 at day 0 by
  `build_basket`). `rotation_ratio = inflow_basket / outflow_basket`. A short/long
  moving-average crossover (`ma_short`/`ma_long`, per-pair) produces `signal` (1 =
  inflow side winning, -1 = outflow side winning) and `signal_change`.
- **`CONFIG["level_indicators"]`**: single-ticker level tracked against fixed
  `zones` (e.g. VIX: normal/warning/panic). No ratio, no inflow/outflow.

### Naming convention gotcha: pairs ① and ② are inverted relative to ③④⑤
For pairs ③(`breadth_smallcap`)④(`breadth_equalweight`)⑤(`credit_risk`), the
first-listed side is `inflow_tickers`, so the ratio rises when that first side wins —
intuitive given each pair's label (e.g. "小型 vs 大型" rises when 小型/small-cap wins).

Pairs ①(`value_tech`)②(`defensive_tech`) were deliberately flipped so **tech is
`inflow_tickers`** — the ratio rises when tech is winning, not when value/defensive
wins, even though "テック" is the *second* word in each pair's `label` ("①テック vs
バリュー/景気循環"). This was an explicit user request (ratio should increase with
tech strength) — do not "fix" this to match the ③④⑤ pattern without checking with the
user first. `top_holdings.py`'s `CATEGORIES` list and `generate_dashboard.py`'s
`compute_regime()` both hard-code which field (`inflow_tickers`/`outflow_tickers`,
`signal == 1`/`== -1`) corresponds to which real-world side for pairs ① and ② — if you
touch the inflow/outflow assignment for those two pairs again, grep both files for
`value_tech` / `defensive_tech` and update the flipped checks there too.

### Output file naming
Files in `output/` are re-generated on every run with today's `YYYYMMDD` stamp (no
cleanup of older dates). Per-pair chart PNGs are prefixed `01_`..`05_` (pair order in
`CONFIG["pairs"]`) and the top-holdings PNG is `06_`, so they sort correctly in a file
browser; the dashboard PNG is deliberately *not* numbered. If a new pair is added,
`plot_rotation.py`'s `main()` assigns the prefix by enumeration order automatically.

### generate_dashboard.py specifics
- `compute_regime()` counts 4 hard-coded "stress" flags (defensive-winning,
  credit-flight-to-quality, breadth-concentration, VIX-elevated) to produce a 3-tier
  overall read (limited / watch / broad risk-off) — this logic only looks at
  `defensive_tech`, `credit_risk`, `breadth_equalweight`, and VIX by name, not all
  pairs generically.
- Each panel shows a "転換直後・要確認" (just-flipped, unconfirmed) badge when
  `signal_change` happened within `CONFIRM_WINDOW_DAYS` (5 trading days), and a
  short/long MA spread mini-gauge (`draw_spread_gauge`) — both computed per-panel from
  the pair's own daily CSV, not from any shared state.

### top_holdings.py specifics
Pulls each category's ETF top-10 holdings via `yfinance.Ticker(t).funds_data.top_holdings`
(no relation to the price-based CSVs) and equal-weights across a category's constituent
ETFs when a stock appears in more than one. Valuation coloring (cheap/neutral/expensive)
is a **composite score**, not a single ratio: `VALUATION_METRICS` (PER, PEG, PBR,
EV/EBITDA, dividend yield, FCF yield) are each converted to a category-relative
percentile rank ("cheapness", 0-1, direction-aware — see each metric's `"direction"`)
and averaged into a 0-100 score (`add_valuation()`); ≥66.7 = 割安, ≤33.3 = 割高, and a
stock needs at least `MIN_METRICS_REQUIRED` valid metrics or it's reported as
判定不能. This is a peer/category-relative read, not a fair-value estimate — a stock
can score "cheap" simply because the rest of its category is even more expensive.
Negative PER/PEG/PBR/EV-EBITDA (e.g. negative book value) are treated as unusable for
that metric, not as "very cheap". This script is not part of `run_pipeline.py` / the
exe; it's run standalone and does its own network calls per stock (~30
`yf.Ticker(...).get_info()` calls for holdings + metrics combined), so it's noticeably
slower than the other three scripts.

### Encoding
Windows PowerShell/cmd default to a non-UTF-8 codepage, which garbles the Japanese
console output. `console_utf8.py` fixes this (sets console codepage to 65001 +
reconfigures `sys.stdout`/`stderr`) and must be the *first* import in every entry-point
script — it already is in all four scripts plus `run_pipeline.py`; keep that ordering
in any new script that prints Japanese text. `.ps1` files are a separate, unrelated
gotcha: Windows PowerShell 5.1 parses non-UTF-8-BOM `.ps1` files using the system
codepage, so `build_exe.ps1` is kept ASCII-only rather than fixed with the same trick.

### Docs that must stay in sync with CONFIG
`readme.txt` (setup/usage) and `dashboard_guide.txt` (how to read the dashboard, incl.
worked examples) both describe the current `CONFIG["pairs"]` contents and the ①②
direction convention above in prose. Any change to basket composition, MA periods, or
the inflow/outflow direction of a pair should be reflected in both files, not just the
code.
