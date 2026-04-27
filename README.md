# EastMoney Sector Flow

A Gemini skill for extracting industry sector fund-flow data from EastMoney (`data.eastmoney.com/bkzj`) using browser automation and Chrome DevTools Protocol (CDP) network interception.

## Core Principle: The Hard Rule

**Never call EastMoney API endpoints directly.**

This project strictly adheres to the rule that all data must be extracted via browser-triggered navigation or physical clicks in Chrome. Data is captured by intercepting CDP `Network` events and retrieving response bodies using `Network.getResponseBody`. This ensures the extraction process mimics real user behavior and bypasses direct API restrictions or potential bot detection on raw HTTP requests.

## Project Structure

- `SKILL.md`: The skill definition and instruction set for Gemini.
- `scripts/eastmoney_browser_extract.py`: The core extraction script designed to be executed via `browser-harness`.
- `agents/openai.yaml`: Configuration for the associated agent (if applicable).

## Usage

Ensure you have `browser-harness` installed and configured.

To run the extraction:

```bash
browser-harness < scripts/eastmoney_browser_extract.py
```

### Script Workflow

1.  **Sector List:**
    - Navigates to the sector fund-flow page.
    - Interacts with pagination (clicks pages 1, 2, and 3).
    - Intercepts `clist/get` XHR requests to build `sectors_list.json`.
2.  **Historical Detail:**
    - For each sector, opens a detail page.
    - Clicks the "行业历史资金流" (Industry Historical Fund Flow) tab.
    - Intercepts `daykline/get` XHR requests to build `sectors_historical_data.json`.
    - Includes retry logic and checkpointing every 10 sectors.

## Configuration

The script can be configured via environment variables:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `EASTMONEY_SECTORS_LIST` | Output path for sector list JSON | `sectors_list.json` |
| `EASTMONEY_HISTORICAL_DATA` | Output path for historical JSON | `sectors_historical_data.json` |
| `EASTMONEY_WAIT_MIN` | Minimum random wait between actions (seconds) | `1.0` |
| `EASTMONEY_WAIT_MAX` | Maximum random wait between actions (seconds) | `3.0` |
| `EASTMONEY_DETAIL_RETRIES` | Retry attempts for missed history sectors | `2` |
| `EASTMONEY_RESUME_HISTORIES`| Resume from existing history checkpoint (`1` or `0`) | `1` |

## Data Schema

### `sectors_list.json`

Captures the current state of industry sectors:
- `sector_code`: Unique identifier (e.g., `BK0447`).
- `sector_name`: Name of the industry.
- `latest_price`, `change_percent`: Market performance.
- `main_net_inflow`, `super_large_net_inflow`, etc.: Fund flow metrics.
- `top_main_net_inflow_stock_name/code`: Leading stock in the sector.

### `sectors_historical_data.json`

Captures daily historical records for each sector:
- `date`: Transaction date.
- `main_net_inflow`, `large_net_inflow`, etc.: Historical flow values.
- `main_net_inflow_percent`, etc.: Flow percentages.
- `latest_price`, `change_percent`: Historical price and change.

## Validation

You can validate the extracted JSON files using the following one-liner:

```bash
python3 -c 'import json; from pathlib import Path; l=json.loads(Path("sectors_list.json").read_text(encoding="utf-8")); h=json.loads(Path("sectors_historical_data.json").read_text(encoding="utf-8")); counts=[len(s.get("history") or []) for s in h.get("sectors", [])]; print("list_count", len(l.get("sectors", []))); print("hist_count", len(h.get("sectors", []))); print("error_count", len(h.get("errors", []))); print("min_records", min(counts) if counts else 0); print("max_records", max(counts) if counts else 0); print("total_records", sum(counts))'
```
