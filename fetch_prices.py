#!/usr/bin/env python3
"""
fetch_prices.py
30銘柄（日本株+米国株）の株価データをyfinanceで取得し、
前日比%中心の一覧HTMLを生成する。

GitHub Actions から毎日実行され、index.html を更新→commit する想定。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).parent
TICKERS_FILE = ROOT / "tickers.json"
OUTPUT_FILE = ROOT / "index.html"
JST = timezone(timedelta(hours=9))


def fetch_one(symbol: str) -> dict | None:
    """1銘柄分の価格情報を取得して dict で返す。失敗時は None。"""
    try:
        tk = yf.Ticker(symbol)
        # 約2ヶ月分の日次データを取得（前日比、5日、1ヶ月 %算出のため）
        hist = tk.history(period="2mo", auto_adjust=False)
        if hist is None or hist.empty or len(hist) < 2:
            return None

        closes = hist["Close"].dropna()
        vols = hist["Volume"].dropna()
        last = float(closes.iloc[-1])
        prev = float(closes.iloc[-2])
        chg = last - prev
        chg_pct = (chg / prev) * 100 if prev else 0.0

        def pct_n_days_ago(n: int) -> float | None:
            if len(closes) <= n:
                return None
            base = float(closes.iloc[-1 - n])
            return ((last - base) / base) * 100 if base else None

        return {
            "symbol": symbol,
            "last": last,
            "prev": prev,
            "chg": chg,
            "chg_pct": chg_pct,
            "chg_5d": pct_n_days_ago(5),
            "chg_1mo": pct_n_days_ago(20),
            "volume": int(vols.iloc[-1]) if not vols.empty else 0,
            "high_52w": float(closes.max()),  # 注: 2ヶ月のため簡易。fetch_52w で精度向上
            "low_52w": float(closes.min()),
            "date": closes.index[-1].strftime("%Y-%m-%d"),
            # tick_time: 直近の引け時刻 (Unix秒)。JS側でタイムゾーン表示に使う
            "tick_time": int(closes.index[-1].timestamp()),
        }
    except Exception as e:
        print(f"[WARN] {symbol}: {e}", file=sys.stderr)
        return None


def fetch_52w(symbol: str) -> tuple[float, float] | None:
    """52週高安を取得（fast_info経由）。"""
    try:
        info = yf.Ticker(symbol).fast_info
        hi = info.get("year_high") or info.get("yearHigh")
        lo = info.get("year_low") or info.get("yearLow")
        if hi and lo:
            return float(hi), float(lo)
    except Exception:
        pass
    return None


def detect_market(symbol: str) -> tuple[str, str]:
    """ティッカーのサフィックスから (market_label, currency_symbol) を返す。"""
    s = symbol.upper()
    if s.endswith(".T"):
        return "JP", "¥"
    if s.endswith(".KS") or s.endswith(".KQ"):
        return "KR", "₩"
    if s.endswith(".HK"):
        return "HK", "HK$"
    if s.endswith(".SS") or s.endswith(".SZ"):
        return "CN", "¥"  # CNY、JPYと同じ表記だが Mkt 列で区別
    if s.endswith(".TW") or s.endswith(".TWO"):
        return "TW", "NT$"
    return "US", "$"


def main() -> int:
    tickers = json.loads(TICKERS_FILE.read_text(encoding="utf-8"))
    rows: list[dict] = []

    for entry in tickers:
        sym = entry["symbol"]
        data = fetch_one(sym)
        if data is None:
            print(f"[SKIP] {sym}")
            continue
        # 52週レンジを上書き（精度向上）
        yr = fetch_52w(sym)
        if yr:
            data["high_52w"], data["low_52w"] = yr
        market, currency = detect_market(sym)
        data["name"] = entry["name"]
        data["sector"] = entry.get("sector", "")
        data["market"] = market
        data["currency"] = currency
        # 52週レンジ内の位置（%）
        if data["high_52w"] > data["low_52w"]:
            data["range_pos"] = (
                (data["last"] - data["low_52w"])
                / (data["high_52w"] - data["low_52w"])
                * 100
            )
        else:
            data["range_pos"] = 50.0
        rows.append(data)

    if not rows:
        print("[ERROR] データ取得失敗。HTMLは更新しません。", file=sys.stderr)
        return 1

    updated_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    html = render_html(rows, updated_at)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"[OK] {len(rows)} 銘柄 / 出力: {OUTPUT_FILE}")
    return 0


def render_html(rows: list[dict], updated_at: str) -> str:
    """ダッシュボードHTMLを生成。データはJSONとして埋め込み、JSでテーブル描画。"""
    data_json = json.dumps(rows, ensure_ascii=False)
    return TEMPLATE.replace("/*__DATA__*/", data_json).replace(
        "__UPDATED__", updated_at
    )


# --- HTML テンプレート ---------------------------------------------------------

TEMPLATE = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<!-- 検索エンジン除外: noindex で検索結果に出ない、nofollow でリンク追跡もさせない -->
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">
<meta name="googlebot" content="noindex, nofollow">
<meta name="bingbot" content="noindex, nofollow">
<!-- リファラー漏洩防止: 外部リンククリック時にこのURLを送らない -->
<meta name="referrer" content="no-referrer">
<!-- ページを5分毎に自動リロード。GHA cronが裏で更新するため、開きっぱなしで最新化される -->
<meta http-equiv="refresh" content="300">
<title>Daily Watchlist</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Serif:ital,wght@0,400;0,500;0,600;1,400&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #f7f5ef;
    --bg-panel: #fdfcf8;
    --ink: #1a1815;
    --ink-soft: #4a463f;
    --ink-faint: #8a857a;
    --rule: #d9d3c4;
    --rule-soft: #e8e3d3;
    --accent: #1f3a5f;
    --up: #0d7c4a;
    --up-bg: #e6f1ea;
    --down: #b8313a;
    --down-bg: #f6e8e9;
    --flat: #6a665c;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: "IBM Plex Sans", system-ui, sans-serif;
    font-size: 14px;
    -webkit-font-smoothing: antialiased;
  }
  .wrap {
    max-width: 1280px;
    margin: 0 auto;
    padding: 48px 32px 80px;
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: end;
    border-bottom: 1px solid var(--ink);
    padding-bottom: 18px;
    margin-bottom: 24px;
  }
  .brand h1 {
    font-family: "IBM Plex Serif", serif;
    font-weight: 600;
    font-size: 32px;
    margin: 0 0 4px;
    letter-spacing: -0.01em;
  }
  .brand .sub {
    font-family: "IBM Plex Serif", serif;
    font-style: italic;
    color: var(--ink-soft);
    font-size: 14px;
  }
  .meta {
    text-align: right;
    font-family: "IBM Plex Mono", monospace;
    font-size: 11px;
    letter-spacing: 0.04em;
    color: var(--ink-soft);
    text-transform: uppercase;
  }
  .meta .stamp { color: var(--ink); font-weight: 500; }

  .controls {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 20px;
    flex-wrap: wrap;
  }
  .tabs { display: inline-flex; border: 1px solid var(--ink); flex-wrap: wrap; }
  .tab {
    padding: 7px 14px;
    background: transparent;
    border: 0;
    cursor: pointer;
    font: 500 11px/1 "IBM Plex Sans", sans-serif;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--ink);
  }
  .tab + .tab { border-left: 1px solid var(--ink); }
  .tab.active { background: var(--ink); color: var(--bg); }
  .tab .ct {
    font: 500 9px "IBM Plex Mono", monospace;
    color: var(--ink-faint);
    margin-left: 5px;
  }
  .tab.active .ct { color: rgba(247,245,239,0.6); }
  .search {
    flex: 1;
    min-width: 200px;
    padding: 8px 12px;
    border: 1px solid var(--rule);
    background: var(--bg-panel);
    font: 400 13px "IBM Plex Mono", monospace;
    color: var(--ink);
  }
  .search::placeholder { color: var(--ink-faint); }
  .search:focus { outline: 1px solid var(--accent); border-color: var(--accent); }

  /* Summary strip */
  .summary {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 0;
    margin-bottom: 24px;
    border: 1px solid var(--rule);
    background: var(--bg-panel);
  }
  .stat { padding: 14px 18px; border-right: 1px solid var(--rule); }
  .stat:last-child { border-right: 0; }
  .stat .label {
    font: 500 10px/1 "IBM Plex Mono", monospace;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--ink-faint);
    margin-bottom: 6px;
  }
  .stat .val {
    font: 500 22px "IBM Plex Serif", serif;
    color: var(--ink);
  }
  .stat .val.up { color: var(--up); }
  .stat .val.down { color: var(--down); }

  /* Table */
  table {
    width: 100%;
    border-collapse: collapse;
    background: var(--bg-panel);
    border: 1px solid var(--rule);
  }
  thead th {
    text-align: right;
    padding: 11px 12px;
    font: 500 10px/1 "IBM Plex Mono", monospace;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--ink-soft);
    border-bottom: 1px solid var(--ink);
    background: var(--bg-panel);
    user-select: none;
    cursor: pointer;
    white-space: nowrap;
    position: sticky;
    top: 0;
    transition: background .15s;
  }
  thead th.left { text-align: left; }
  thead th:hover { color: var(--ink); background: #f0ebd9; }
  thead th .arrow {
    opacity: 0;
    margin-left: 6px;
    display: inline-block;
    font-size: 13px;
    line-height: 1;
    transition: transform .15s;
  }
  thead th.sort-asc,
  thead th.sort-desc {
    color: var(--accent);
    background: #ecebe0;
  }
  thead th.sort-asc .arrow,
  thead th.sort-desc .arrow { opacity: 1; }
  thead th.sort-asc .arrow { transform: rotate(180deg); }
  tbody tr.top-mover td.sym::before {
    content: "▲";
    font-size: 9px;
    color: var(--up);
    margin-right: 4px;
  }
  tbody tr.worst-mover td.sym::before {
    content: "▼";
    font-size: 9px;
    color: var(--down);
    margin-right: 4px;
  }
  tbody tr { border-bottom: 1px solid var(--rule-soft); }
  tbody tr:hover { background: rgba(31, 58, 95, 0.04); }
  tbody td {
    padding: 10px 12px;
    text-align: right;
    font: 400 13px "IBM Plex Mono", monospace;
    color: var(--ink);
    vertical-align: middle;
  }
  td.left { text-align: left; }
  td.sym {
    font-weight: 600;
    color: var(--accent);
    letter-spacing: 0.02em;
  }
  td.name {
    font-family: "IBM Plex Sans", sans-serif;
    color: var(--ink);
    max-width: 200px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  td.sector {
    font-family: "IBM Plex Sans", sans-serif;
    font-size: 11px;
    color: var(--ink-faint);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  td.market {
    font-size: 10px;
    letter-spacing: 0.1em;
    color: var(--ink-faint);
  }
  .pct { display: inline-block; min-width: 60px; padding: 3px 8px; text-align: right; font-weight: 500; }
  .pct.up { background: var(--up-bg); color: var(--up); }
  .pct.down { background: var(--down-bg); color: var(--down); }
  .pct.flat { color: var(--flat); }
  .pct-soft.up { color: var(--up); }
  .pct-soft.down { color: var(--down); }
  .pct-soft.flat { color: var(--flat); }

  /* 52w range bar */
  .range {
    position: relative;
    width: 88px;
    height: 4px;
    background: var(--rule);
    margin-left: auto;
  }
  .range::after {
    content: "";
    position: absolute;
    top: -2px;
    width: 2px;
    height: 8px;
    background: var(--accent);
    left: calc(var(--p, 50%) - 1px);
  }

  .sort-bar {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
    font: 500 10px/1 "IBM Plex Mono", monospace;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--ink-faint);
  }
  .sort-bar .quick {
    display: inline-flex;
    gap: 6px;
  }
  .sort-bar .qbtn {
    padding: 5px 10px;
    border: 1px solid var(--rule);
    background: var(--bg-panel);
    font: 500 10px/1 "IBM Plex Mono", monospace;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--ink-soft);
    cursor: pointer;
  }
  .sort-bar .qbtn:hover { border-color: var(--ink); color: var(--ink); }
  .sort-bar .qbtn.active { background: var(--ink); color: var(--bg); border-color: var(--ink); }
  .sort-bar .current {
    color: var(--accent);
    font-weight: 600;
  }

  /* Live badge */
  .live-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border: 1px solid var(--rule);
    background: var(--bg-panel);
    font: 500 10px/1 "IBM Plex Mono", monospace;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--ink-soft);
  }
  .live-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--ink-faint);
    display: inline-block;
  }
  .live-dot.snap { background: var(--accent); }
  @keyframes pulse {
    0%   { box-shadow: 0 0 0 0 rgba(13, 124, 74, 0.5); }
    70%  { box-shadow: 0 0 0 7px rgba(13, 124, 74, 0); }
    100% { box-shadow: 0 0 0 0 rgba(13, 124, 74, 0); }
  }
  .last-poll { color: var(--ink-faint); margin-left: 4px; }
  .retry-btn {
    margin-left: 6px;
    padding: 2px 8px;
    border: 1px solid var(--rule);
    background: var(--bg-panel);
    font: 500 9px "IBM Plex Mono", monospace;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    cursor: pointer;
    color: var(--ink-soft);
  }
  .retry-btn:hover { color: var(--ink); border-color: var(--ink); }

  /* Market status banner */
  .markets {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 0;
    margin-bottom: 20px;
    border: 1px solid var(--rule);
    background: var(--bg-panel);
  }
  .mkt-card {
    padding: 10px 12px;
    border-right: 1px solid var(--rule);
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .mkt-card:last-child { border-right: 0; }
  .mkt-row {
    display: flex;
    align-items: center;
    gap: 6px;
    font: 500 11px/1 "IBM Plex Sans", sans-serif;
  }
  .mkt-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--ink-faint);
  }
  .mkt-open .mkt-dot { background: var(--up); animation: pulse 2.5s infinite; }
  .mkt-extended .mkt-dot { background: #d68a00; }
  .mkt-closed .mkt-dot { background: var(--ink-faint); }
  .mkt-name {
    font-weight: 600;
    letter-spacing: 0.06em;
    color: var(--ink);
  }
  .mkt-state {
    font: 500 9px "IBM Plex Mono", monospace;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--ink-soft);
  }
  .mkt-open .mkt-state { color: var(--up); }
  .mkt-extended .mkt-state { color: #d68a00; }
  .mkt-count {
    margin-left: auto;
    font: 500 10px "IBM Plex Mono", monospace;
    color: var(--ink-faint);
  }
  .mkt-time {
    display: flex;
    flex-direction: column;
    font: 400 10px/1.4 "IBM Plex Mono", monospace;
    color: var(--ink-soft);
  }
  .mkt-time .local { color: var(--ink); font-weight: 500; }
  .mkt-time .jst { color: var(--ink-faint); }

  /* Flash animation for price changes */
  @keyframes flash-up   { 0% { background: rgba(13, 124, 74, 0.25); } 100% { background: transparent; } }
  @keyframes flash-down { 0% { background: rgba(184, 49, 58, 0.25); } 100% { background: transparent; } }
  tbody tr.flash-up   { animation: flash-up   1.2s ease-out; }
  tbody tr.flash-down { animation: flash-down 1.2s ease-out; }

  /* Section header */
  .section-head {
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin: 32px 0 12px;
  }
  .section-head h2 {
    font: 500 18px "IBM Plex Serif", serif;
    margin: 0;
    letter-spacing: -0.01em;
  }
  .section-head .count {
    font: 500 11px "IBM Plex Mono", monospace;
    color: var(--ink-faint);
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }
  .section-head .rule { flex: 1; height: 1px; background: var(--rule); }

  footer {
    margin-top: 40px;
    padding-top: 16px;
    border-top: 1px solid var(--rule);
    font: 400 11px/1.6 "IBM Plex Mono", monospace;
    color: var(--ink-faint);
    letter-spacing: 0.04em;
  }
  footer a { color: var(--ink-soft); }

  @media (max-width: 800px) {
    .wrap { padding: 24px 16px 48px; }
    .summary { grid-template-columns: repeat(2, 1fr); }
    .markets { grid-template-columns: repeat(3, 1fr); }
    .mkt-card:nth-child(3n) { border-right: 0; }
    .mkt-card:nth-child(n+4) { border-top: 1px solid var(--rule); }
    .stat:nth-child(2) { border-right: 0; }
    .stat:nth-child(1), .stat:nth-child(2) { border-bottom: 1px solid var(--rule); }
    td.sector, th.col-sector { display: none; }
    td.range-cell, th.col-range { display: none; }
    td.chg1mo, th.col-1mo { display: none; }
    .brand h1 { font-size: 24px; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">
      <h1>Daily Watchlist</h1>
      <div class="sub">Global tech &amp; semi · server-side snapshot · sorted by day-over-day</div>
    </div>
    <div class="meta">
      <div class="live-badge" id="liveBadge">
        <span class="live-dot snap"></span> <span>Snapshot</span>
      </div>
      <div style="margin-top:6px">Updated: <span class="stamp">__UPDATED__</span></div>
    </div>
  </header>

  <div class="markets" id="marketStatus"></div>

  <div class="controls">
    <div class="tabs" role="tablist" id="tabs"></div>
    <input id="search" class="search" type="text" placeholder="Filter: symbol or name…" autocomplete="off">
  </div>

  <div class="summary" id="summary"></div>

  <div class="sort-bar">
    <span>Sort by:</span>
    <div class="quick">
      <button class="qbtn" data-sort="chg_pct">Day %</button>
      <button class="qbtn" data-sort="chg_5d">5D %</button>
      <button class="qbtn" data-sort="chg_1mo">1M %</button>
      <button class="qbtn" data-sort="volume">Volume</button>
      <button class="qbtn" data-sort="symbol">Symbol</button>
    </div>
    <span style="margin-left:auto">Active: <span class="current" id="sortLabel"></span></span>
  </div>

  <div id="tableMount"></div>

  <footer>
    Source: Yahoo Finance via yfinance. Prices may be delayed 15–20 minutes.
    Auto-generated daily by GitHub Actions. For research only, not investment advice.
  </footer>
</div>

<script>
const DATA = /*__DATA__*/;
let filter = "all";
let query = "";
let sortKey = "chg_pct";
let sortDir = -1; // -1 desc, 1 asc

// === Market timezone + hours (local) ===
const MARKET_INFO = {
  JP: { tz: "Asia/Tokyo",       label: "JST", openH: 9, openM: 0,  closeH: 15, closeM: 30 },
  KR: { tz: "Asia/Seoul",       label: "KST", openH: 9, openM: 0,  closeH: 15, closeM: 30 },
  TW: { tz: "Asia/Taipei",      label: "TWT", openH: 9, openM: 0,  closeH: 13, closeM: 30 },
  HK: { tz: "Asia/Hong_Kong",   label: "HKT", openH: 9, openM: 30, closeH: 16, closeM: 0  },
  CN: { tz: "Asia/Shanghai",    label: "CST", openH: 9, openM: 30, closeH: 15, closeM: 0  },
  US: { tz: "America/New_York", label: "ET",  openH: 9, openM: 30, closeH: 16, closeM: 0  },
};

// === Formatters ===
const fmtNum = (n, d=2) => n == null ? "—" :
  n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtPrice = (n, ccy) => {
  if (n == null) return "—";
  const d = (ccy === "¥" && n >= 1000) ? 0 : 2;
  return ccy + n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
};
const fmtVol = (n) => {
  if (!n) return "—";
  if (n >= 1e9) return (n/1e9).toFixed(2) + "B";
  if (n >= 1e6) return (n/1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n/1e3).toFixed(1) + "K";
  return n.toString();
};
const cls = (p) => p == null ? "flat" : p > 0.005 ? "up" : p < -0.005 ? "down" : "flat";
const sign = (p) => p == null ? "" : p > 0 ? "+" : "";

// Format unix-seconds timestamp in given timezone
function tsAt(unixSec, tz) {
  if (!unixSec) return "—";
  const d = new Date(unixSec * 1000);
  try {
    return d.toLocaleTimeString("ja-JP", {
      timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false,
    });
  } catch (e) { return "—"; }
}
function dateAt(unixSec, tz) {
  if (!unixSec) return "—";
  const d = new Date(unixSec * 1000);
  try {
    return d.toLocaleDateString("ja-JP", {
      timeZone: tz, month: "2-digit", day: "2-digit",
    });
  } catch (e) { return "—"; }
}

// === Compute current market state in browser ===
function getLocalParts(tz, date = new Date()) {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: tz, hour12: false,
    weekday: "short", hour: "2-digit", minute: "2-digit",
  });
  const parts = fmt.formatToParts(date);
  const get = t => parts.find(p => p.type === t)?.value;
  return {
    weekday: get("weekday"),                 // "Mon" ~ "Sun"
    hour: parseInt(get("hour"), 10),
    minute: parseInt(get("minute"), 10),
  };
}
function currentMarketState(market) {
  const info = MARKET_INFO[market];
  if (!info) return "CLOSED";
  const p = getLocalParts(info.tz);
  if (p.weekday === "Sat" || p.weekday === "Sun") return "CLOSED";
  const nowMin   = p.hour * 60 + p.minute;
  const openMin  = info.openH  * 60 + info.openM;
  const closeMin = info.closeH * 60 + info.closeM;
  if (nowMin >= openMin && nowMin <= closeMin) return "REGULAR";
  return "CLOSED";
}

// === Per-market status banner ===
function renderMarketStatus() {
  const byMarket = {};
  for (const r of DATA) (byMarket[r.market] ||= []).push(r);
  const order = ["JP", "KR", "CN", "HK", "TW", "US"];
  const html = order.filter(m => byMarket[m]).map(m => {
    const rows = byMarket[m];
    const latest = Math.max(...rows.map(r => r.tick_time || 0));
    const state = currentMarketState(m);
    const stateLabel = state === "REGULAR" ? "Open" : "Closed";
    const cls = state === "REGULAR" ? "mkt-open" : "mkt-closed";
    const info = MARKET_INFO[m];
    const local = tsAt(latest, info.tz);
    const jst = tsAt(latest, "Asia/Tokyo");
    const jstDate = dateAt(latest, "Asia/Tokyo");
    const todayJst = dateAt(Math.floor(Date.now()/1000), "Asia/Tokyo");
    const dateSuffix = jstDate !== todayJst ? ` (${jstDate})` : "";
    return `
      <div class="mkt-card ${cls}">
        <div class="mkt-row">
          <span class="mkt-dot"></span>
          <span class="mkt-name">${m}</span>
          <span class="mkt-state">${stateLabel}</span>
          <span class="mkt-count">${rows.length}</span>
        </div>
        <div class="mkt-time">
          <span class="local">${local} ${info.label}</span>
          <span class="jst">${jst} JST${dateSuffix}</span>
        </div>
      </div>`;
  }).join("");
  document.getElementById("marketStatus").innerHTML = html;
}

// === Rendering ===
function rowsFiltered() {
  let rs = DATA.slice();
  if (filter !== "all") rs = rs.filter(r => r.market === filter);
  if (query) {
    const q = query.toLowerCase();
    rs = rs.filter(r =>
      r.symbol.toLowerCase().includes(q) ||
      r.name.toLowerCase().includes(q) ||
      (r.sector || "").toLowerCase().includes(q)
    );
  }
  rs.sort((a, b) => {
    let va = a[sortKey], vb = b[sortKey];
    if (typeof va === "string") return sortDir * va.localeCompare(vb);
    if (va == null) return 1;
    if (vb == null) return -1;
    return sortDir * (va - vb);
  });
  return rs;
}

function renderSummary() {
  const rs = rowsFiltered();
  const valid = rs.filter(r => r.chg_pct != null);
  const advancers = valid.filter(r => r.chg_pct > 0).length;
  const decliners = valid.filter(r => r.chg_pct < 0).length;
  const avg = valid.length ? valid.reduce((s,r)=>s+r.chg_pct,0)/valid.length : 0;
  const sorted = [...valid].sort((a,b) => b.chg_pct - a.chg_pct);
  const top = sorted[0], bot = sorted[sorted.length-1];

  document.getElementById("summary").innerHTML = `
    <div class="stat">
      <div class="label">Avg day-over-day</div>
      <div class="val ${cls(avg)}">${sign(avg)}${fmtNum(avg,2)}%</div>
    </div>
    <div class="stat">
      <div class="label">Advancers / Decliners</div>
      <div class="val"><span style="color:var(--up)">${advancers}</span> <span style="color:var(--ink-faint);font-size:14px">/</span> <span style="color:var(--down)">${decliners}</span></div>
    </div>
    <div class="stat">
      <div class="label">Top mover</div>
      <div class="val ${cls(top?.chg_pct)}" title="${top?.name||''}">
        ${top ? top.symbol + ' ' + sign(top.chg_pct) + fmtNum(top.chg_pct,2) + '%' : '—'}
      </div>
    </div>
    <div class="stat">
      <div class="label">Worst</div>
      <div class="val ${cls(bot?.chg_pct)}" title="${bot?.name||''}">
        ${bot ? bot.symbol + ' ' + sign(bot.chg_pct) + fmtNum(bot.chg_pct,2) + '%' : '—'}
      </div>
    </div>
  `;
}

function renderTable() {
  const rs = rowsFiltered();
  const cols = [
    { k: "symbol",  label: "Symbol",  left: true },
    { k: "name",    label: "Name",    left: true },
    { k: "sector",  label: "Sector",  left: true, extraClass: "col-sector" },
    { k: "market",  label: "Mkt",     left: true },
    { k: "last",    label: "Last" },
    { k: "chg_pct", label: "Day %" },
    { k: "chg_5d",  label: "5D %" },
    { k: "chg_1mo", label: "1M %",   extraClass: "col-1mo" },
    { k: "volume",  label: "Volume" },
    { k: "range_pos", label: "52W Range", extraClass: "col-range" },
  ];

  const colLabel = cols.find(c => c.k === sortKey)?.label || sortKey;
  const sl = document.getElementById("sortLabel");
  if (sl) sl.textContent = `${colLabel} ${sortDir < 0 ? "↓ DESC" : "↑ ASC"}`;
  document.querySelectorAll(".qbtn").forEach(b => {
    b.classList.toggle("active", b.dataset.sort === sortKey);
  });

  const valid = rs.filter(r => r.chg_pct != null);
  const sorted = [...valid].sort((a,b) => b.chg_pct - a.chg_pct);
  const topSym = sorted[0]?.symbol;
  const botSym = sorted[sorted.length-1]?.symbol;

  const thead = `<thead><tr>${cols.map(c => {
    const sortCls = c.k === sortKey ? (sortDir < 0 ? "sort-desc" : "sort-asc") : "";
    return `<th class="${c.left ? 'left' : ''} ${c.extraClass||''} ${sortCls}" data-k="${c.k}">${c.label}<span class="arrow">▼</span></th>`;
  }).join("")}</tr></thead>`;

  const tbody = `<tbody>${rs.map(r => {
    const trCls = r.symbol === topSym ? "top-mover" : r.symbol === botSym ? "worst-mover" : "";
    const tickLocal = tsAt(r.tick_time, MARKET_INFO[r.market]?.tz);
    const tickJst = tsAt(r.tick_time, "Asia/Tokyo");
    const tooltip = `Last tick: ${tickLocal} ${MARKET_INFO[r.market]?.label || ''} (${tickJst} JST)`;
    return `
    <tr class="${trCls}">
      <td class="left sym">${r.symbol}</td>
      <td class="left name" title="${r.name}">${r.name}</td>
      <td class="left sector col-sector">${r.sector || ''}</td>
      <td class="left market">${r.market}</td>
      <td title="${tooltip}">${fmtPrice(r.last, r.currency)}</td>
      <td><span class="pct ${cls(r.chg_pct)}">${sign(r.chg_pct)}${fmtNum(r.chg_pct,2)}%</span></td>
      <td class="pct-soft ${cls(r.chg_5d)}">${sign(r.chg_5d)}${fmtNum(r.chg_5d,1)}%</td>
      <td class="pct-soft ${cls(r.chg_1mo)} chg1mo">${sign(r.chg_1mo)}${fmtNum(r.chg_1mo,1)}%</td>
      <td>${fmtVol(r.volume)}</td>
      <td class="range-cell"><div class="range" style="--p:${(r.range_pos||50).toFixed(0)}%" title="${fmtNum(r.range_pos,0)}% of 52w range"></div></td>
    </tr>`;
  }).join("")}</tbody>`;

  document.getElementById("tableMount").innerHTML = `<table>${thead}${tbody}</table>`;

  document.querySelectorAll("th[data-k]").forEach(th => {
    th.addEventListener("click", () => {
      const k = th.dataset.k;
      if (k === sortKey) sortDir = -sortDir; else { sortKey = k; sortDir = -1; }
      renderTable();
    });
  });
}

function rerender() { renderSummary(); renderTable(); }

function renderTabs() {
  const order = ["all", "US", "JP", "TW", "KR", "CN", "HK"];
  const counts = { all: DATA.length };
  for (const r of DATA) counts[r.market] = (counts[r.market] || 0) + 1;
  const labels = { all: "All", US: "US", JP: "JP", TW: "TW", KR: "KR", CN: "CN", HK: "HK" };
  const html = order
    .filter(k => k === "all" || counts[k])
    .map(k => `<button class="tab ${filter===k?'active':''}" data-filter="${k}">${labels[k]}<span class="ct">${counts[k]||0}</span></button>`)
    .join("");
  document.getElementById("tabs").innerHTML = html;
  document.querySelectorAll(".tab").forEach(b => {
    b.addEventListener("click", () => {
      filter = b.dataset.filter;
      renderTabs();
      rerender();
    });
  });
}

document.getElementById("search").addEventListener("input", e => {
  query = e.target.value.trim();
  rerender();
});
document.querySelectorAll(".qbtn").forEach(b => {
  b.addEventListener("click", () => {
    const k = b.dataset.sort;
    if (k === sortKey) sortDir = -sortDir; else { sortKey = k; sortDir = -1; }
    rerender();
  });
});

// === Boot ===
renderTabs();
rerender();
renderMarketStatus();
// Re-render market status banner every minute so Open/Closed labels stay current
setInterval(renderMarketStatus, 60_000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
