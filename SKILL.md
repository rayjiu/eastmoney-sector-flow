---
name: eastmoney-sector-flow
description: Extract EastMoney sector fund-flow data from data.eastmoney.com/bkzj using browser-harness and Chrome CDP network interception. Use when Codex needs EastMoney industry sector list data, sector fund-flow rankings, or historical sector fund-flow detail data, especially requests mentioning bkzj, hy.html, sectors_list.json, sectors_historical_data.json, push2 clist, push2his daykline, or the hard requirement that all data come from browser-triggered navigation/clicks plus Network.getResponseBody rather than direct API calls.
---

# EastMoney Sector Flow

## Hard Rule

Never call EastMoney API endpoints directly with `fetch()`, `curl`, `urllib`, `requests`, `http_get`, `urlopen`, `aiohttp`, `httpx`, or similar HTTP clients.

All sector list and historical data must come from browser-triggered navigation or physical clicks in Chrome, CDP `Network` events, and `Network.getResponseBody` for intercepted request IDs.

## Quick Start

Use the `browser-harness` skill for browser control.

Work in the user's requested workspace. If `scripts/eastmoney_browser_extract.py` already exists, inspect it briefly, treat it as the local source of truth, and run:

```bash
browser-harness < scripts/eastmoney_browser_extract.py
```

If the script is missing, copy this skill's bundled script into the workspace and keep it there. Resolve the bundled script relative to this skill's root directory, not from a hard-coded install path. For example, from the skill root directory containing `SKILL.md`:

```bash
mkdir -p "$TARGET_WORKSPACE/scripts"
rsync -a scripts/eastmoney_browser_extract.py "$TARGET_WORKSPACE/scripts/eastmoney_browser_extract.py"
cd "$TARGET_WORKSPACE"
browser-harness < scripts/eastmoney_browser_extract.py
```

If EastMoney changed, patch the workspace script while preserving the hard rule. Do not delete the script afterward.

## Expected Browser Workflow

### Sector List

1. Navigate Chrome to `https://data.eastmoney.com/bkzj/hy.html`.
2. Enable CDP network monitoring with `Network.enable` before actions.
3. Because page 1 may already be loaded, click footer pagination page 2 once as setup.
4. Physically click footer pagination buttons 1, 2, and 3.
5. For each click, intercept `push2.eastmoney.com/api/qt/clist/get`.
6. Match the clicked page with `pn=<page>` and `fs=m:90 s:4`.
7. Read the response with `Network.getResponseBody`, parse JSONP or JSON, and write `sectors_list.json`.

### Historical Detail

1. For every `sector_code` in `sectors_list.json`, open `https://data.eastmoney.com/bkzj/<SectorCode>.html` in a detail tab.
2. Enable CDP network monitoring before navigating the detail tab.
3. Capture `push2his.eastmoney.com/api/qt/stock/fflow/daykline/get`.
4. Match `secid=90.<SectorCode>`.
5. Read the response with `Network.getResponseBody`.
6. Physically click the `行业历史资金流` tab for each sector.
7. If the tab or response is missed, retry the sector with longer waits and relaxed tab matching.
8. Close each detail tab after capture; leaving one sector-list control tab is fine.
9. Write checkpoints every 10 sectors and save final output to `sectors_historical_data.json`.

Use random 1-3 second waits between clicks and navigations.

## Script Requirements

The reusable script should preserve equivalent functions for:

- `parse_jsonp`
- `collect_matching_bodies` from CDP `Network` events
- `find_pager`
- `find_history_tab`
- `capture_sector_list`
- `capture_sector_history`
- `retry_history_errors`
- JSON writing and checkpointing

If patching capture logic, keep `Network.enable` before navigation/clicks, drain stale events before a target action, match URLs by request parameters, and call `Network.getResponseBody` only on intercepted browser request IDs.

Useful environment variables supported by the bundled script:

- `EASTMONEY_SECTORS_LIST`: output path for sector list JSON; default `sectors_list.json`
- `EASTMONEY_HISTORICAL_DATA`: output path for historical JSON; default `sectors_historical_data.json`
- `EASTMONEY_WAIT_MIN` and `EASTMONEY_WAIT_MAX`: random wait bounds; defaults `1.0` and `3.0`
- `EASTMONEY_DETAIL_RETRIES`: retry attempts for missed history sectors; default `2`
- `EASTMONEY_RESUME_HISTORIES`: set to `0` to ignore an existing history checkpoint

## Field Mappings

Sector list rows:

- `f12` -> `sector_code`
- `f14` -> `sector_name`
- `f2` -> `latest_price`
- `f3` -> `change_percent`
- `f62` -> `main_net_inflow`
- `f184` -> `main_net_inflow_percent`
- `f66` -> `super_large_net_inflow`
- `f69` -> `super_large_net_inflow_percent`
- `f72` -> `large_net_inflow`
- `f75` -> `large_net_inflow_percent`
- `f78` -> `medium_net_inflow`
- `f81` -> `medium_net_inflow_percent`
- `f84` -> `small_net_inflow`
- `f87` -> `small_net_inflow_percent`
- `f204` -> `top_main_net_inflow_stock_name`
- `f205` -> `top_main_net_inflow_stock_code`

Historical `daykline` records parse into:

- `date`
- `main_net_inflow`
- `small_net_inflow`
- `medium_net_inflow`
- `large_net_inflow`
- `super_large_net_inflow`
- `main_net_inflow_percent`
- `small_net_inflow_percent`
- `medium_net_inflow_percent`
- `large_net_inflow_percent`
- `super_large_net_inflow_percent`
- `latest_price`
- `change_percent`

## Final Validation

After extraction, report:

- `sectors_list.json` sector count
- `sectors_historical_data.json` sector count
- error count
- min and max historical record count per sector
- total historical records

Use local JSON parsing for validation. Example:

```bash
python3 -c 'import json; from pathlib import Path; l=json.loads(Path("sectors_list.json").read_text(encoding="utf-8")); h=json.loads(Path("sectors_historical_data.json").read_text(encoding="utf-8")); counts=[len(s.get("history") or []) for s in h.get("sectors", [])]; print("list_count", len(l.get("sectors", []))); print("hist_count", len(h.get("sectors", []))); print("error_count", len(h.get("errors", []))); print("min_records", min(counts) if counts else 0); print("max_records", max(counts) if counts else 0); print("total_records", sum(counts))'
```
