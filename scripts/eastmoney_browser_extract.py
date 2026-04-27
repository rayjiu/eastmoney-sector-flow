"""EastMoney sector fund-flow extractor driven by browser CDP events.

Run from this workspace with:

    browser-harness < scripts/eastmoney_browser_extract.py

This script intentionally does not call EastMoney API endpoints directly. It
uses browser-harness helpers to navigate/click in Chrome, enables CDP Network
monitoring, and extracts response bodies with Network.getResponseBody.
"""

import base64
import json
import os
import random
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

LIST_URL = "https://data.eastmoney.com/bkzj/hy.html"
OUT_LIST = os.environ.get("EASTMONEY_SECTORS_LIST", "sectors_list.json")
OUT_HIST = os.environ.get("EASTMONEY_HISTORICAL_DATA", "sectors_historical_data.json")

RANDOM_MIN = float(os.environ.get("EASTMONEY_WAIT_MIN", "1.0"))
RANDOM_MAX = float(os.environ.get("EASTMONEY_WAIT_MAX", "3.0"))
DETAIL_RETRY_ATTEMPTS = int(os.environ.get("EASTMONEY_DETAIL_RETRIES", "2"))
RESUME_HISTORIES = os.environ.get("EASTMONEY_RESUME_HISTORIES", "1") != "0"


def log(message):
    print(message, flush=True)


def natural_wait(context):
    seconds = random.uniform(RANDOM_MIN, RANDOM_MAX)
    log("[wait] %.2fs %s" % (seconds, context))
    wait(seconds)


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat()


def url_param(url, name):
    try:
        values = parse_qs(urlparse(url).query).get(name)
        if values:
            return values[0]
    except Exception:
        return ""
    return ""


def body_to_text(body_result):
    text = body_result.get("body", "")
    if body_result.get("base64Encoded"):
        return base64.b64decode(text).decode("utf-8", errors="replace")
    return text


def enable_network_monitoring():
    cdp("Network.enable")
    try:
        cdp("Network.setCacheDisabled", cacheDisabled=True)
    except Exception as ex:
        log("[warn] could not disable cache: %s" % ex)


def parse_jsonp(text):
    s = (text or "").strip()
    if not s:
        raise ValueError("empty response body")
    if s[0] == "{" or s[0] == "[":
        return json.loads(s)
    left = s.find("(")
    right = s.rfind(")")
    if left < 0 or right <= left:
        raise ValueError("not JSONP/JSON: " + s[:80])
    return json.loads(s[left + 1:right])


def is_sector_list_url(url, page):
    if "api/qt/clist/get" not in url:
        return False
    if url_param(url, "pn") != str(page):
        return False
    fs = url_param(url, "fs")
    if fs == "m:90 s:4" or fs == "m:90+s:4":
        return True
    return "fs=m%3A90+s%3A4" in url or "fs=m:90+s:4" in url


def is_daykline_url(url, sector_code):
    if "stock/fflow/daykline/get" not in url:
        return False
    secid = url_param(url, "secid")
    return secid == "90." + sector_code or ("secid=90." + sector_code) in url


def collect_matching_bodies(predicate, timeout, label):
    records = {}
    bodies = []
    deadline = time.time() + timeout

    while time.time() < deadline:
        for event in drain_events():
            method = event.get("method")
            params = event.get("params", {})
            request_id = params.get("requestId")
            if not request_id:
                continue

            rec = records.get(request_id)
            if rec is None:
                rec = {"requestId": request_id, "session_id": event.get("session_id")}
                records[request_id] = rec

            if method == "Network.requestWillBeSent":
                rec["request_url"] = params.get("request", {}).get("url", "")
                rec["session_id"] = event.get("session_id")
            elif method == "Network.responseReceived":
                rec["response_url"] = params.get("response", {}).get("url", "")
                rec["status"] = params.get("response", {}).get("status")
                rec["type"] = params.get("type")
                rec["session_id"] = event.get("session_id")
                url = rec.get("response_url") or rec.get("request_url") or ""
                try:
                    if predicate(url):
                        rec["matched"] = True
                except Exception as ex:
                    rec["predicate_error"] = str(ex)
            elif method == "Network.loadingFinished":
                rec["finished"] = True
                rec["session_id"] = event.get("session_id")
            elif method == "Network.loadingFailed":
                rec["failed"] = params.get("errorText", "failed")
                rec["session_id"] = event.get("session_id")

        for request_id in list(records.keys()):
            rec = records[request_id]
            if not rec.get("matched") or rec.get("body_done"):
                continue
            if not rec.get("finished") and time.time() < deadline - 0.5:
                continue

            try:
                body_result = cdp("Network.getResponseBody", session_id=rec.get("session_id"), requestId=request_id)
                text = body_to_text(body_result)
                out = {}
                out.update(rec)
                out["body"] = text
                out["body_length"] = len(text)
                bodies.append(out)
                rec["body_done"] = True
                log("[capture] %s %s bytes from %s" % (label, len(text), rec.get("response_url") or rec.get("request_url")))
            except Exception as ex:
                rec["body_error"] = str(ex)
                rec["body_done"] = True

        if bodies:
            return bodies
        wait(0.25)

    return bodies


def find_pager(page):
    wanted = json.dumps(str(page), ensure_ascii=False)
    expression = r"""
(()=>{
  const wanted = WANTED;
  for (const e of Array.from(document.querySelectorAll('a'))) {
    const t = (e.innerText || e.textContent || '').trim();
    if (t === wanted) {
      e.scrollIntoView({block:'center', inline:'center'});
      const r = e.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) {
        return {x:r.left+r.width/2, y:r.top+r.height/2, text:t, className:e.className};
      }
    }
  }
  return null;
})()
""".replace("WANTED", wanted)
    return js(expression)


def find_pager_with_retry(page, timeout=15.0, interval=0.5):
    deadline = time.time() + timeout
    attempt = 0
    while True:
        attempt += 1
        coord = find_pager(page)
        if coord:
            if attempt > 1:
                log("[pager] located page %s on attempt %s" % (page, attempt))
            return coord
        if time.time() >= deadline:
            return None
        wait(interval)


def find_history_tab(relaxed=False):
    contains_clause = " || t.endsWith('历史资金流') || t.includes('行业历史资金流')" if relaxed else ""
    expression = r"""
(()=>{
  const els = Array.from(document.querySelectorAll('li,a,span,button,div'));
  for (const e of els) {
    const t = (e.innerText || e.textContent || '').trim().replace(/\s+/g,' ');
    const matches = (t === '行业历史资金流')CONTAINS_CLAUSE;
    if (!matches) continue;
    e.scrollIntoView({block:'center', inline:'center'});
    const r = e.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) {
      return {x:r.left+r.width/2, y:r.top+r.height/2, text:t, className:e.className, tag:e.tagName};
    }
  }
  return null;
})()
""".replace("CONTAINS_CLAUSE", contains_clause)
    return js(expression)


def dismiss_possible_overlay():
    expression = r"""
(()=>{
  for (const e of Array.from(document.querySelectorAll('a,button,div,span'))) {
    const t = (e.innerText || e.textContent || '').trim();
    const cls = String(e.className || '');
    const r = e.getBoundingClientRect();
    const visible = r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0 && r.top < innerHeight && r.left < innerWidth;
    if (!visible) continue;
    if (t === '关闭' || t === '×' || /close/i.test(cls)) {
      return {x:r.left+r.width/2, y:r.top+r.height/2, text:t, className:cls};
    }
  }
  return null;
})()
"""
    coord = js(expression)
    if coord:
        click(coord["x"], coord["y"])
        natural_wait("after overlay close click")
        return True
    return False


def map_sector(row, page, page_rank):
    return {
        "sector_code": row.get("f12"),
        "sector_name": row.get("f14"),
        "latest_price": row.get("f2"),
        "change_percent": row.get("f3"),
        "main_net_inflow": row.get("f62"),
        "main_net_inflow_percent": row.get("f184"),
        "super_large_net_inflow": row.get("f66"),
        "super_large_net_inflow_percent": row.get("f69"),
        "large_net_inflow": row.get("f72"),
        "large_net_inflow_percent": row.get("f75"),
        "medium_net_inflow": row.get("f78"),
        "medium_net_inflow_percent": row.get("f81"),
        "small_net_inflow": row.get("f84"),
        "small_net_inflow_percent": row.get("f87"),
        "top_main_net_inflow_stock_name": row.get("f204"),
        "top_main_net_inflow_stock_code": row.get("f205"),
        "top_main_net_inflow_stock_market": row.get("f206"),
        "market_id": row.get("f13"),
        "quote_status": row.get("f1"),
        "update_timestamp": row.get("f124"),
        "page": page,
        "page_rank": page_rank,
    }


def parse_sector_page(body, page):
    payload = parse_jsonp(body)
    rows = (payload.get("data") or {}).get("diff") or []
    sectors = []
    rank = 1
    for row in rows:
        sectors.append(map_sector(row, page, rank))
        rank += 1
    return payload, sectors


def parse_number(parts, index):
    value = parts[index] if index < len(parts) else None
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return value


def parse_daykline_record(line):
    parts = line.split(",")
    while len(parts) < 15:
        parts.append(None)

    return {
        "date": parts[0],
        "main_net_inflow": parse_number(parts, 1),
        "small_net_inflow": parse_number(parts, 2),
        "medium_net_inflow": parse_number(parts, 3),
        "large_net_inflow": parse_number(parts, 4),
        "super_large_net_inflow": parse_number(parts, 5),
        "main_net_inflow_percent": parse_number(parts, 6),
        "small_net_inflow_percent": parse_number(parts, 7),
        "medium_net_inflow_percent": parse_number(parts, 8),
        "large_net_inflow_percent": parse_number(parts, 9),
        "super_large_net_inflow_percent": parse_number(parts, 10),
        "latest_price": parse_number(parts, 11),
        "change_percent": parse_number(parts, 12),
        "unknown_f64": parse_number(parts, 13),
        "unknown_f65": parse_number(parts, 14),
    }


def parse_history_body(body):
    payload = parse_jsonp(body)
    data = payload.get("data") or {}
    records = []
    for line in data.get("klines") or []:
        records.append(parse_daykline_record(line))
    return payload, records


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def target_exists(target_id):
    if not target_id:
        return False
    try:
        targets = cdp("Target.getTargets").get("targetInfos") or []
    except Exception:
        return False
    for target in targets:
        if target.get("targetId") == target_id and target.get("type") == "page":
            return True
    return False


def ensure_control_tab(target_id=None):
    if target_exists(target_id):
        try:
            switch_tab(target_id)
            return target_id
        except Exception as ex:
            log("[warn] control tab switch failed: %s" % ex)

    log("[tab] creating replacement control tab")
    return new_tab("about:blank")


def close_detail_tab(detail_target, control_target, code):
    if not detail_target:
        return control_target
    if target_exists(detail_target):
        try:
            cdp("Target.closeTarget", targetId=detail_target)
            log("[close] detail tab %s" % code)
        except Exception as close_ex:
            log("[close-error] %s %s" % (code, close_ex))
    else:
        log("[close-skip] detail tab %s already closed" % code)
    return ensure_control_tab(control_target)


def capture_sector_list():
    log("[nav] list page " + LIST_URL)
    list_target = new_tab("about:blank")
    enable_network_monitoring()
    drain_events()
    goto(LIST_URL)
    wait_for_load(20)
    natural_wait("after list navigation")
    dismiss_possible_overlay()

    # Start on page 2 so the required page 1, 2, 3 clicks all trigger fresh XHR/script loads.
    coord = find_pager_with_retry(2, timeout=20.0)
    if not coord:
        raise RuntimeError("could not locate setup pager 2")
    log("[click] setup pager 2 at %.1f, %.1f" % (coord["x"], coord["y"]))
    drain_events()
    click(coord["x"], coord["y"])
    natural_wait("after setup page 2 click")
    wait(0.5)
    drain_events()

    sectors = []
    page_records = []
    seen_codes = set()

    for page in [1, 2, 3]:
        coord = find_pager_with_retry(page, timeout=15.0)
        if not coord:
            raise RuntimeError("could not locate pager %s" % page)
        log("[click] sector-list pager %s at %.1f, %.1f" % (page, coord["x"], coord["y"]))
        drain_events()
        click(coord["x"], coord["y"])
        natural_wait("after sector-list page %s click" % page)

        def predicate(url, page=page):
            return is_sector_list_url(url, page)

        bodies = collect_matching_bodies(predicate, 8.0, "sector-list page %s" % page)
        if not bodies:
            raise RuntimeError("no clist/get response captured for page %s" % page)

        body_rec = bodies[0]
        payload, page_sectors = parse_sector_page(body_rec["body"], page)
        page_records.append({
            "page": page,
            "captured_url": body_rec.get("response_url") or body_rec.get("request_url"),
            "response_length": body_rec.get("body_length"),
            "row_count": len(page_sectors),
            "total": (payload.get("data") or {}).get("total"),
        })
        for sector in page_sectors:
            code = sector.get("sector_code")
            if code and code not in seen_codes:
                seen_codes.add(code)
                sectors.append(sector)
        log("[list] page %s rows=%s cumulative=%s" % (page, len(page_sectors), len(sectors)))

    output = {
        "source": "EastMoney sector fund flow",
        "source_url": LIST_URL,
        "captured_at": now_iso(),
        "collection_method": "Browser UI pagination clicks with CDP Network.getResponseBody; no direct endpoint calls",
        "pages": page_records,
        "sectors": sectors,
    }
    write_json(OUT_LIST, output)
    log("[write] %s sectors=%s" % (OUT_LIST, len(sectors)))
    return list_target, sectors


def capture_sector_history(control_target, sector, relaxed=False):
    code = sector.get("sector_code")
    name = sector.get("sector_name")
    detail_url = "https://data.eastmoney.com/bkzj/" + str(code) + ".html"
    detail_target = None
    result = None
    error = None

    try:
        control_target = ensure_control_tab(control_target)
        natural_wait("before detail navigation %s" % code)
        detail_target = new_tab("about:blank")
        enable_network_monitoring()
        drain_events()
        log("[nav] detail %s %s" % (code, name))
        goto(detail_url)
        wait_for_load(25)
        natural_wait("after detail navigation %s" % code)

        def predicate(url, code=code):
            return is_daykline_url(url, code)

        bodies = collect_matching_bodies(predicate, 8.0 if relaxed else 6.0, "daykline %s" % code)

        coord = find_history_tab(relaxed=relaxed)
        if not coord and not relaxed:
            wait(1.0)
            coord = find_history_tab(relaxed=True)
        if not coord:
            raise RuntimeError("historical tab not found")

        log("[click] historical tab %s at %.1f, %.1f" % (code, coord["x"], coord["y"]))
        click(coord["x"], coord["y"])
        natural_wait("after historical tab click %s" % code)

        if not bodies:
            more = collect_matching_bodies(predicate, 8.0 if relaxed else 5.0, "daykline-after-click %s" % code)
            for item in more:
                bodies.append(item)
        if not bodies:
            raise RuntimeError("no daykline/get response captured")

        body_rec = bodies[0]
        payload, history_records = parse_history_body(body_rec["body"])
        result = {
            "sector_code": code,
            "sector_name": name,
            "detail_url": detail_url,
            "captured_url": body_rec.get("response_url") or body_rec.get("request_url"),
            "response_length": body_rec.get("body_length"),
            "record_count": len(history_records),
            "market": (payload.get("data") or {}).get("market"),
            "history": history_records,
        }
        log("[history] %s %s records=%s" % (code, name, len(history_records)))
    except Exception as ex:
        log("[error] %s %s %s" % (code, name, ex))
        error = {
            "sector_code": code,
            "sector_name": name,
            "detail_url": detail_url,
            "error": str(ex),
        }

    control_target = close_detail_tab(detail_target, control_target, code)
    return result, error, control_target


def make_historical_output():
    return {
        "source": "EastMoney sector historical fund flow",
        "source_url_pattern": "https://data.eastmoney.com/bkzj/{sector_code}.html",
        "captured_at": now_iso(),
        "collection_method": "Browser detail-page navigation plus physical historical-tab click; CDP Network.getResponseBody on intercepted daykline responses; no direct endpoint calls",
        "field_order_note": "daykline fields2 parsed as date, main, small, medium, large, super_large, main_pct, small_pct, medium_pct, large_pct, super_large_pct, latest_price, change_pct, unknown_f64, unknown_f65",
        "sectors": [],
        "errors": [],
    }


def sort_historical(hist, sectors):
    order = {}
    index = 0
    for sector in sectors:
        order[sector.get("sector_code")] = index
        index += 1

    def key(item, order=order):
        return order.get(item.get("sector_code"), 99999)

    hist["sectors"].sort(key=key)


def upsert_history(hist, result):
    code = result.get("sector_code")
    hist["sectors"] = [item for item in hist.get("sectors", []) if item.get("sector_code") != code]
    hist["sectors"].append(result)


def load_historical_checkpoint(sectors):
    hist = make_historical_output()
    if not RESUME_HISTORIES or not os.path.exists(OUT_HIST):
        return hist

    try:
        with open(OUT_HIST, "r", encoding="utf-8") as f:
            previous = json.load(f)
    except Exception as ex:
        log("[resume] could not read checkpoint: %s" % ex)
        return hist

    wanted_codes = {sector.get("sector_code") for sector in sectors}
    seen = set()
    for item in previous.get("sectors") or []:
        code = item.get("sector_code")
        if not code or code not in wanted_codes or code in seen:
            continue
        if item.get("record_count", len(item.get("history") or [])) <= 0:
            continue
        seen.add(code)
        hist["sectors"].append(item)

    hist["captured_at"] = now_iso()
    sort_historical(hist, sectors)
    if hist["sectors"]:
        log("[resume] loaded checkpoint successes=%s; prior errors ignored for fresh retry" % len(hist["sectors"]))
    return hist


def captured_codes(hist):
    return {item.get("sector_code") for item in hist.get("sectors", []) if item.get("sector_code")}


def capture_all_histories(list_target, sectors):
    control_target = ensure_control_tab(list_target)
    hist = load_historical_checkpoint(sectors)
    write_json(OUT_HIST, hist)

    total = len(sectors)
    for index, sector in enumerate(sectors, start=1):
        if sector.get("sector_code") in captured_codes(hist):
            log("[skip] %s/%s %s already captured" % (index, total, sector.get("sector_code")))
            continue
        log("[detail] %s/%s %s" % (index, total, sector.get("sector_code")))
        result, error, control_target = capture_sector_history(control_target, sector, relaxed=False)
        if result:
            upsert_history(hist, result)
        if error:
            hist["errors"].append(error)
        if result or error or index % 10 == 0 or index == total:
            hist["captured_at"] = now_iso()
            sort_historical(hist, sectors)
            write_json(OUT_HIST, hist)
            if index % 10 == 0 or index == total or error:
                log("[checkpoint] wrote %s sectors=%s errors=%s" % (OUT_HIST, len(hist["sectors"]), len(hist["errors"])))

    return hist, control_target


def retry_history_errors(list_target, sectors, hist):
    control_target = ensure_control_tab(list_target)
    for attempt in range(1, DETAIL_RETRY_ATTEMPTS + 1):
        errors = hist.get("errors") or []
        if not errors:
            break

        by_code = {}
        for sector in sectors:
            by_code[sector.get("sector_code")] = sector

        log("[retry] attempt=%s sectors=%s" % (attempt, len(errors)))
        remaining = []
        for err in errors:
            sector = by_code.get(err.get("sector_code")) or err
            result, retry_error, control_target = capture_sector_history(control_target, sector, relaxed=True)
            if result:
                upsert_history(hist, result)
            else:
                new_err = {}
                new_err.update(err)
                new_err["retry_error"] = retry_error.get("error") if retry_error else "unknown retry error"
                remaining.append(new_err)
            hist["captured_at"] = now_iso()
            sort_historical(hist, sectors)
            write_json(OUT_HIST, hist)

        hist["errors"] = remaining
        hist["captured_at"] = now_iso()
        sort_historical(hist, sectors)
        write_json(OUT_HIST, hist)
        log("[retry-checkpoint] wrote %s sectors=%s errors=%s" % (OUT_HIST, len(hist["sectors"]), len(hist["errors"])))

    return hist


# browser-harness execs this file inside its runner function. Make imports and
# sibling helpers visible as globals for functions called after exec-time setup.
globals().update(locals())

log("[start] EastMoney sector flow extraction via browser CDP")
drain_events()
list_tab, sector_rows = capture_sector_list()
historical, list_tab = capture_all_histories(list_tab, sector_rows)
historical = retry_history_errors(list_tab, sector_rows, historical)
sort_historical(historical, sector_rows)
historical["captured_at"] = now_iso()
write_json(OUT_HIST, historical)
log("[done] sectors=%s histories=%s errors=%s" % (len(sector_rows), len(historical["sectors"]), len(historical["errors"])))
