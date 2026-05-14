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
    """1銘柄分の価格情報を取得して dict で返す。失敗時は None。
    
    戦略:
    - tk.fast_info から「現在価格 (last_price)」「前日終値 (previous_close)」を優先取得
      → これにより yfinance が「今日の値動き」を正しく反映できる
    - tk.history() は 5D/1M リターン算出と 52週レンジのみに使う
    """
    try:
        tk = yf.Ticker(symbol)

        # fast_info: 最新の last_price と previous_close (引け前/引け後問わず最新)
        fast_last = None
        fast_prev = None
        market_cap = None
        pe_ratio = None
        high_52w_fast = None
        low_52w_fast = None
        try:
            fi = tk.fast_info
            fast_last = fi.get("last_price") or fi.get("lastPrice")
            fast_prev = fi.get("previous_close") or fi.get("previousClose")
            mc = fi.get("market_cap") or fi.get("marketCap")
            if mc:
                market_cap = float(mc)
            pe = fi.get("trailing_pe") or fi.get("trailingPE")
            if pe is not None and pe > 0 and pe < 10000:
                pe_ratio = float(pe)
            high_52w_fast = fi.get("year_high") or fi.get("yearHigh")
            low_52w_fast = fi.get("year_low") or fi.get("yearLow")
        except Exception:
            pass

        # PER の fallback: fast_info にない場合は .info
        if pe_ratio is None:
            try:
                info = tk.info
                if info and isinstance(info, dict):
                    pe = info.get("trailingPE") or info.get("forwardPE")
                    if pe is not None and pe > 0 and pe < 10000:
                        pe_ratio = float(pe)
            except Exception:
                pass

        # 日次履歴 (5D/1M リターン + 52週レンジ算出用)
        # auto_adjust=False で「未調整値」を取得 → 業務でよく見る生の終値
        hist = tk.history(period="2mo", auto_adjust=False)
        if hist is None or hist.empty or len(hist) < 2:
            return None

        closes = hist["Close"].dropna()
        vols = hist["Volume"].dropna()

        # last は fast_info を優先 (最新の取引値が反映されるため)
        # fast_info から取れない場合は履歴の最終行から
        if fast_last is not None and fast_last > 0:
            last = float(fast_last)
        else:
            last = float(closes.iloc[-1])

        # prev は履歴の「未調整終値」を必ず使用 (fast_info.previous_close は配当落ち調整される
        # ことがあり、業務で見る前日比とズレるため)
        # last が履歴の最終値と同じ場合 → 履歴の最後から2番目を prev に
        # last が履歴の最終値と違う場合 (fast_info の方が新しい) → 履歴の最終値を prev に
        last_close = float(closes.iloc[-1])
        if abs(last - last_close) < 0.01 * last_close:  # 同じ値とみなせる場合
            prev = float(closes.iloc[-2])
        else:
            prev = last_close

        chg = last - prev
        chg_pct = (chg / prev) * 100 if prev else 0.0

        # 5D / 1M リターンは履歴ベース (last は fast_info の最新値を使用)
        def pct_n_days_ago(n: int) -> float | None:
            if len(closes) <= n:
                return None
            base = float(closes.iloc[-1 - n])
            return ((last - base) / base) * 100 if base else None

        # 52週高安: fast_info を優先、なければ履歴の最大/最小
        high_52w = float(high_52w_fast) if high_52w_fast else float(closes.max())
        low_52w = float(low_52w_fast) if low_52w_fast else float(closes.min())

        # tick_time: fast_info の場合は現時点、履歴フォールバック時は引け時刻
        if fast_last is not None and fast_last > 0:
            from datetime import datetime as _dt
            tick_time = int(_dt.now().timestamp())
        else:
            tick_time = int(closes.index[-1].timestamp())

        return {
            "symbol": symbol,
            "last": last,
            "prev": prev,
            "chg": chg,
            "chg_pct": chg_pct,
            "chg_5d": pct_n_days_ago(5),
            "chg_1mo": pct_n_days_ago(20),
            "volume": int(vols.iloc[-1]) if not vols.empty else 0,
            "high_52w": high_52w,
            "low_52w": low_52w,
            "date": closes.index[-1].strftime("%Y-%m-%d"),
            "tick_time": tick_time,
            "market_cap": market_cap,
            "pe_ratio": pe_ratio,
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


# ============================================================
# 市場セッション判定: 「ザラ場中(値が変動する状態)ではない」ことを確認
# True = 取得安全 (週末 / 開場前 / 引け30分後以降)
# False = ザラ場中 (値が不安定なため前回値を保持)
# ============================================================
# (tz_offset_hours, open_h, open_m, close_h, close_m)
MARKET_SCHEDULE = {
    "JP": (9,  9, 0,  15, 30),  # 9:00-15:30 JST
    "KR": (9,  9, 0,  15, 30),  # 9:00-15:30 KST
    "TW": (8,  9, 0,  13, 30),  # 9:00-13:30 TWT
    "HK": (8,  9, 30, 16, 0),   # 9:30-16:00 HKT
    "CN": (8,  9, 30, 15, 0),   # 9:30-15:00 CST
    "US": (-5, 9, 30, 16, 0),   # 9:30-16:00 ET (EST, 夏時間は実質-4だが許容)
}


def is_safe_to_fetch(market: str) -> bool:
    """指定市場について「今取得して安定した確定値が取れるか」を判定。
    取得安全 = 市場がザラ場中ではない（週末 / 開場前 / 引け+30分以降）
    取得不安全 = ザラ場中 → 値が変動するので取得しない
    """
    from datetime import datetime, timezone, timedelta
    if market not in MARKET_SCHEDULE:
        return True
    tz_off, oh, om, ch, cm = MARKET_SCHEDULE[market]
    tz = timezone(timedelta(hours=tz_off))
    now_local = datetime.now(timezone.utc).astimezone(tz)
    # 週末は土日 → 取得可 (金曜の確定値を取れる)
    if now_local.weekday() >= 5:
        return True
    today_open = now_local.replace(hour=oh, minute=om, second=0, microsecond=0)
    today_close = now_local.replace(hour=ch, minute=cm, second=0, microsecond=0)
    close_plus_buffer = today_close + timedelta(minutes=30)
    # 開場前 OR 引け30分後以降 → 安全
    return now_local < today_open or now_local >= close_plus_buffer


def load_previous_data() -> dict[str, dict]:
    """前回 commit された index.html から銘柄データを抽出 (差分更新用)。
    market_close=False のときに前回の値を保持するため。"""
    if not OUTPUT_FILE.exists():
        return {}
    try:
        html = OUTPUT_FILE.read_text(encoding="utf-8")
        # JSON データ部を取り出す
        import re
        m = re.search(
            r'<script id="__data__"[^>]*>(.*?)</script>',
            html, re.DOTALL
        )
        if not m:
            return {}
        rows = json.loads(m.group(1))
        return {r["symbol"]: r for r in rows}
    except Exception as e:
        print(f"[WARN] 前回データの読み込み失敗: {e}", file=sys.stderr)
        return {}


def fetch_fx_rates() -> dict[str, float]:
    """各通貨→USD のレートを取得。失敗時は1.0でフォールバック。"""
    # yfinance のシンボル: USDJPY=X は "1USD = N JPY"。逆算で 1JPY = 1/N USD。
    pairs = {
        "JPY": "JPY=X",   # USDJPY → 1JPY = 1/rate USD
        "KRW": "KRW=X",
        "CNY": "CNY=X",
        "HKD": "HKD=X",
        "TWD": "TWD=X",
    }
    fx = {"USD": 1.0}
    for ccy, sym in pairs.items():
        try:
            tk = yf.Ticker(sym)
            hist = tk.history(period="5d", auto_adjust=False)
            if hist is not None and not hist.empty:
                rate = float(hist["Close"].dropna().iloc[-1])
                if rate > 0:
                    fx[ccy] = 1.0 / rate
                    continue
        except Exception:
            pass
        # フォールバック値（おおよそ）
        fallback = {"JPY": 1/150, "KRW": 1/1370, "CNY": 1/7.2, "HKD": 1/7.8, "TWD": 1/32}
        fx[ccy] = fallback.get(ccy, 1.0)
    return fx


# 通貨マッピング
MARKET_TO_CCY = {"JP": "JPY", "KR": "KRW", "CN": "CNY", "HK": "HKD", "TW": "TWD", "US": "USD"}


def main() -> int:
    tickers = json.loads(TICKERS_FILE.read_text(encoding="utf-8"))
    print("[INFO] Fetching FX rates...")
    fx = fetch_fx_rates()
    print(f"[INFO] FX rates (per USD): " + ", ".join(f"{k}={v:.6f}" for k, v in fx.items()))

    # 前回データを読み込む (市場が開いている間は前回の確定値を保持する)
    prev_data = load_previous_data()
    print(f"[INFO] Previous data loaded: {len(prev_data)} tickers")

    # 各市場の取得可否をログ出力
    market_safe = {m: is_safe_to_fetch(m) for m in MARKET_SCHEDULE}
    print(f"[INFO] Safe to fetch (no active session): " + ", ".join(
        f"{m}={'YES' if c else 'NO'}" for m, c in market_safe.items()
    ))

    rows: list[dict] = []
    fetched_count = 0
    kept_count = 0

    for entry in tickers:
        sym = entry["symbol"]
        market, currency = detect_market(sym)

        # ザラ場中の市場 → 取得しない、前回値を保持
        if not market_safe.get(market, True):
            if sym in prev_data:
                # 前回データを保持 (セクター・メモは tickers.json から最新を反映)
                row = dict(prev_data[sym])
                # セクター/メモは tickers.json で更新される可能性があるため上書き
                sectors = entry.get("sectors")
                if sectors is None and "sector" in entry:
                    sectors = [entry["sector"]]
                row["sectors"] = sectors or []
                row["note"] = entry.get("note", "")
                row["name"] = entry["name"]  # 表示名も追従
                # FX レートだけは最新で再換算 (USD表示の精度向上)
                ccy = MARKET_TO_CCY.get(market, "USD")
                mc = row.get("market_cap")
                if mc:
                    row["market_cap_usd"] = mc * fx.get(ccy, 1.0)
                rows.append(row)
                kept_count += 1
                continue
            else:
                # 前回データがない場合のみ取得を試みる (初回 push 時など)
                print(f"[INFO] {sym}: market open but no prev data, will fetch")

        # 市場が閉場している、または初回 → 取得
        data = fetch_one(sym)
        if data is None:
            # 取得失敗時は前回データを保持
            if sym in prev_data:
                row = dict(prev_data[sym])
                sectors = entry.get("sectors")
                if sectors is None and "sector" in entry:
                    sectors = [entry["sector"]]
                row["sectors"] = sectors or []
                row["note"] = entry.get("note", "")
                row["name"] = entry["name"]
                rows.append(row)
                kept_count += 1
                print(f"[KEEP] {sym}: fetch failed, kept previous data")
            else:
                print(f"[SKIP] {sym}: fetch failed, no prev data")
            continue

        yr = fetch_52w(sym)
        if yr:
            data["high_52w"], data["low_52w"] = yr
        data["name"] = entry["name"]
        sectors = entry.get("sectors")
        if sectors is None and "sector" in entry:
            sectors = [entry["sector"]]
        data["sectors"] = sectors or []
        data["note"] = entry.get("note", "")
        data["market"] = market
        data["currency"] = currency
        ccy = MARKET_TO_CCY.get(market, "USD")
        mc = data.get("market_cap")
        if mc:
            data["market_cap_usd"] = mc * fx.get(ccy, 1.0)
        else:
            data["market_cap_usd"] = None
        if data["high_52w"] > data["low_52w"]:
            data["range_pos"] = (
                (data["last"] - data["low_52w"])
                / (data["high_52w"] - data["low_52w"])
                * 100
            )
        else:
            data["range_pos"] = 50.0
        rows.append(data)
        fetched_count += 1

    if not rows:
        print("[ERROR] データ取得失敗。HTMLは更新しません。", file=sys.stderr)
        return 1

    print(f"[INFO] {fetched_count} fetched / {kept_count} kept from previous / {len(rows)} total")
    updated_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    html = render_html(rows, updated_at, fx)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"[OK] {len(rows)} 銘柄 / 出力: {OUTPUT_FILE}")
    return 0


def _sanitize(v):
    """JSON-safe な値に変換。NaN/Infinity は None に。"""
    import math
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
    return v


def _clean_row(row: dict) -> dict:
    """全フィールドを JSON-safe にクリーン化。"""
    return {k: _sanitize(v) for k, v in row.items()}


def render_html(rows: list[dict], updated_at: str, fx: dict[str, float] = None) -> str:
    """ダッシュボードHTMLを生成。データはJSONとして埋め込み、JSでテーブル描画。"""
    # NaN/Infinity を None に変換してから JSON 化（JS パース時のエラー防止）
    # default=str は使わない（数値型まで文字列化されて JS で計算エラーになるため）
    clean_rows = [_clean_row(r) for r in rows]
    data_json = json.dumps(clean_rows, ensure_ascii=False, allow_nan=False)
    # </script> 文字列がデータに混入していた場合の防御
    data_json = data_json.replace("</", "<\\/")
    # Build FX rate display string for footer (1 USD = N XXX format)
    fx_str = ""
    if fx:
        order = ["JPY", "KRW", "CNY", "HKD", "TWD"]
        fmt = lambda v: f"{1/v:,.2f}" if v and v > 0 else "—"
        parts = [f"1 USD = {fmt(fx.get(c))} {c}" for c in order if c in fx]
        fx_str = " · ".join(parts)
    return (
        TEMPLATE
        .replace("/*__DATA__*/", data_json)
        .replace("__UPDATED__", updated_at)
        .replace("__FX_RATES__", fx_str)
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
<title>AI &amp; Semiconductor Watchlist</title>
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
    line-height: 1.25;
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

  /* Sector chips */
  .chips {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 4px;
    max-width: 100%;
  }
  .chip {
    display: inline-block;
    padding: 2px 7px;
    background: var(--rule-soft);
    color: var(--ink-soft);
    font: 500 10px/1.4 "IBM Plex Sans", sans-serif;
    letter-spacing: 0.02em;
    border-radius: 2px;
    white-space: nowrap;
  }
  .chip.active {
    background: var(--accent);
    color: var(--bg);
  }
  /* Sector filter pills above table - collapsed by default */
  .sector-bar {
    margin: 10px 0 14px;
    padding: 10px 12px;
    background: var(--bg-panel);
    border: 1px solid var(--rule);
  }
  .sector-bar .toggle-row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .sector-bar .toggle-btn {
    background: transparent;
    border: 0;
    color: var(--ink);
    font: 500 11px "IBM Plex Sans", sans-serif;
    letter-spacing: 0.04em;
    cursor: pointer;
    padding: 4px 8px 4px 0;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .sector-bar .toggle-btn .arrow {
    display: inline-block;
    transition: transform 0.15s;
    font-size: 9px;
    color: var(--ink-faint);
  }
  .sector-bar .toggle-btn.open .arrow { transform: rotate(180deg); }
  .sector-bar .active-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px 8px 4px 10px;
    background: var(--accent);
    color: var(--bg);
    font: 500 11px "IBM Plex Sans", sans-serif;
    border-radius: 2px;
  }
  .sector-bar .active-chip .close {
    cursor: pointer;
    font-weight: 500;
    opacity: 0.8;
    margin-left: 4px;
  }
  .sector-bar .active-chip .close:hover { opacity: 1; }
  .sector-bar .panel {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--rule-soft);
  }
  .sector-bar .panel.hidden { display: none; }
  .sector-bar .chip {
    cursor: pointer;
    padding: 4px 9px;
    font-size: 11px;
  }
  .sector-bar .chip:hover { background: var(--rule); }

  /* Add chip button in table cells */
  .add-chip {
    display: inline-block;
    padding: 2px 6px;
    background: transparent;
    border: 1px dashed var(--ink-faint);
    color: var(--ink-faint);
    font: 500 10px/1.4 "IBM Plex Sans", sans-serif;
    border-radius: 2px;
    cursor: pointer;
    white-space: nowrap;
  }
  .add-chip:hover {
    border-color: var(--ink);
    color: var(--ink);
  }
  .chip-remove {
    cursor: pointer;
    margin-left: 4px;
    opacity: 0.5;
    font-size: 9px;
  }
  .chip-remove:hover { opacity: 1; }

  td.sectors-cell { max-width: 240px; }
  td.mcap { font: 500 12px "IBM Plex Mono", monospace; }

  /* Note column - now editable */
  td.note-cell {
    font-family: "IBM Plex Sans", sans-serif;
    font-size: 12px;
    color: var(--ink-soft);
    font-style: italic;
    max-width: 240px;
    cursor: text;
  }
  td.note-cell:hover {
    background: rgba(31, 58, 95, 0.04);
  }
  td.note-cell .note-display {
    display: inline-block;
    min-height: 16px;
    min-width: 80px;
    padding: 2px 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 220px;
  }
  td.note-cell .note-display:empty::before {
    content: "click to add note";
    color: var(--ink-faint);
    font-style: normal;
    font-size: 11px;
  }
  td.note-cell input.note-input {
    width: 220px;
    padding: 4px 6px;
    border: 1px solid var(--accent);
    background: var(--bg);
    font: 400 12px "IBM Plex Sans", sans-serif;
    color: var(--ink);
    outline: none;
  }

  /* Save bar fixed at bottom when there are unsaved edits */
  .save-bar {
    position: sticky;
    bottom: 0;
    left: 0;
    right: 0;
    background: var(--ink);
    color: var(--bg);
    padding: 12px 20px;
    display: flex;
    align-items: center;
    gap: 16px;
    border-top: 2px solid var(--accent);
    z-index: 100;
    box-shadow: 0 -4px 10px rgba(0,0,0,0.1);
  }
  .save-bar .msg {
    flex: 1;
    font: 500 13px "IBM Plex Sans", sans-serif;
  }
  .save-bar .msg .count {
    background: var(--accent);
    padding: 2px 8px;
    border-radius: 2px;
    font-family: "IBM Plex Mono", monospace;
    font-size: 11px;
    margin-right: 6px;
  }
  .save-bar button {
    padding: 7px 16px;
    background: var(--bg);
    color: var(--ink);
    border: 0;
    cursor: pointer;
    font: 500 12px/1 "IBM Plex Sans", sans-serif;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .save-bar button:hover { background: var(--bg-panel); }
  .save-bar button.secondary {
    background: transparent;
    color: var(--bg);
    border: 1px solid rgba(247,245,239,0.3);
  }
  .save-bar button.secondary:hover { background: rgba(247,245,239,0.1); }

  /* Modal for add-sector */
  .modal-backdrop {
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.4);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 200;
  }
  .modal-backdrop.hidden { display: none; }
  .modal {
    background: var(--bg);
    border: 1px solid var(--ink);
    padding: 24px;
    width: 360px;
    max-width: 90vw;
  }
  .modal h3 {
    margin: 0 0 4px;
    font: 500 16px "IBM Plex Serif", serif;
  }
  .modal .target {
    font: 400 12px "IBM Plex Mono", monospace;
    color: var(--ink-soft);
    margin-bottom: 14px;
  }
  .modal label {
    font: 500 10px/1 "IBM Plex Mono", monospace;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--ink-faint);
    display: block;
    margin-bottom: 6px;
  }
  .modal input[type="text"] {
    width: 100%;
    padding: 8px 10px;
    border: 1px solid var(--rule);
    background: var(--bg);
    font: 400 13px "IBM Plex Sans", sans-serif;
    color: var(--ink);
    outline: none;
    box-sizing: border-box;
  }
  .modal input[type="text"]:focus { border-color: var(--accent); }
  .modal .existing {
    margin-top: 12px;
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .modal .existing .chip {
    cursor: pointer;
    padding: 3px 8px;
  }
  .modal .actions {
    display: flex;
    gap: 8px;
    margin-top: 18px;
    justify-content: flex-end;
  }
  .modal .actions button {
    padding: 7px 14px;
    font: 500 11px "IBM Plex Sans", sans-serif;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    cursor: pointer;
    border: 1px solid var(--ink);
  }
  .modal .actions button.primary {
    background: var(--ink);
    color: var(--bg);
  }
  .modal .actions button.secondary {
    background: transparent;
    color: var(--ink);
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
  footer .fx-rates {
    margin-top: 6px;
    font-size: 10px;
    color: var(--ink-faint);
    opacity: 0.75;
    letter-spacing: 0.02em;
  }

  /* Mobile card layout (replaces table on small screens) */
  .mobile-cards { display: none; }
  .mcard {
    background: var(--bg-panel);
    border: 1px solid var(--rule);
    margin-bottom: 8px;
    padding: 12px;
  }
  .mcard.top-mover { border-left: 3px solid var(--up); }
  .mcard.worst-mover { border-left: 3px solid var(--down); }
  .mcard-top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 8px;
    margin-bottom: 8px;
  }
  .mcard-name {
    font: 500 15px/1.3 "IBM Plex Sans", sans-serif;
    color: var(--ink);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    min-width: 0;
  }
  .mcard-sym {
    font: 500 11px "IBM Plex Mono", monospace;
    color: var(--accent);
    margin-top: 2px;
  }
  .mcard-mkt {
    font-size: 9px;
    color: var(--ink-faint);
    letter-spacing: 0.08em;
    margin-left: 5px;
  }
  .mcard-pct {
    text-align: right;
    flex-shrink: 0;
  }
  .mcard-pct .pct-big {
    font: 500 20px "IBM Plex Mono", monospace;
    line-height: 1;
  }
  .mcard-pct .pct-big.up { color: var(--up); }
  .mcard-pct .pct-big.down { color: var(--down); }
  .mcard-pct .pct-big.flat { color: var(--flat); }
  .mcard-pct .price {
    font: 400 11px "IBM Plex Mono", monospace;
    color: var(--ink-soft);
    margin-top: 4px;
  }
  .mcard-meta {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 6px;
    margin-bottom: 8px;
    padding: 8px 0;
    border-top: 1px solid var(--rule-soft);
    border-bottom: 1px solid var(--rule-soft);
  }
  .mcard-meta .mc {
    text-align: center;
  }
  .mcard-meta .mc-label {
    font: 500 9px "IBM Plex Mono", monospace;
    color: var(--ink-faint);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin-bottom: 2px;
  }
  .mcard-meta .mc-val {
    font: 500 12px "IBM Plex Mono", monospace;
    color: var(--ink);
  }
  .mcard-meta .mc-val.up { color: var(--up); }
  .mcard-meta .mc-val.down { color: var(--down); }
  .mcard-sectors {
    margin: 6px 0;
  }
  .mcard-note {
    font: 400 12px/1.4 "IBM Plex Sans", sans-serif;
    color: var(--ink-soft);
    font-style: italic;
    padding: 6px 0 0;
    border-top: 1px dashed var(--rule-soft);
    margin-top: 4px;
    min-height: 22px;
    cursor: pointer;
  }
  .mcard-note:empty::before, .mcard-note.empty::before {
    content: "📝 メモを追加…";
    color: var(--ink-faint);
    font-style: normal;
    font-size: 11px;
  }
  .mcard-note input {
    width: 100%;
    border: 0;
    background: transparent;
    font: inherit;
    color: var(--ink);
    padding: 2px 0;
    outline: 1px solid var(--accent);
  }

  @media (max-width: 800px) {
    .wrap { padding: 16px 12px 40px; }
    .summary { grid-template-columns: repeat(2, 1fr); }
    .markets { grid-template-columns: repeat(3, 1fr); gap: 0; }
    .mkt-card { padding: 8px 10px; }
    .mkt-card:nth-child(3n) { border-right: 0; }
    .mkt-card:nth-child(n+4) { border-top: 1px solid var(--rule); }
    .stat { padding: 10px 12px; }
    .stat:nth-child(2) { border-right: 0; }
    .stat:nth-child(1), .stat:nth-child(2) { border-bottom: 1px solid var(--rule); }
    .brand h1 { font-size: 22px; }
    .brand .sub { font-size: 12px; }
    header { flex-direction: column; align-items: flex-start; gap: 8px; }
    .meta { text-align: left; }
    /* Hide desktop table, show mobile cards */
    #tableMount table { display: none; }
    .mobile-cards { display: block; }
    /* Compact controls */
    .controls { flex-direction: column; align-items: stretch; gap: 8px; }
    .tabs { width: 100%; justify-content: space-between; }
    .tab { padding: 8px 6px; flex: 1; text-align: center; font-size: 10px; }
    .sort-bar { flex-wrap: wrap; }
    .sort-bar .quick { width: 100%; overflow-x: auto; flex-wrap: nowrap; }
    .sort-bar .qbtn { white-space: nowrap; flex-shrink: 0; }
    .sort-bar > span:last-child { width: 100%; }
    /* Save bar mobile */
    .save-bar { left: 12px; right: 12px; bottom: 12px; }
    .save-bar .msg { font-size: 12px; }
    /* Modal full-width on mobile */
    .modal { max-width: calc(100vw - 24px); }
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">
      <h1>AI &amp; Semiconductor Watchlist</h1>
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
      <button class="qbtn" data-sort="market_cap_usd">Mkt Cap ($)</button>
      <button class="qbtn" data-sort="pe_ratio">PER</button>
      <button class="qbtn" data-sort="volume">Volume</button>
      <button class="qbtn" data-sort="symbol">Symbol</button>
    </div>
    <span style="margin-left:auto">Active: <span class="current" id="sortLabel"></span></span>
  </div>

  <div class="sector-bar" id="sectorBar"></div>

  <div id="tableMount"></div>
  <div class="mobile-cards" id="mobileCards"></div>

  <footer>
    <div>Source: Yahoo Finance via yfinance. Prices may be delayed 15–20 minutes.
    Auto-generated by GitHub Actions. For research only, not investment advice.</div>
    <div class="fx-rates">FX (USD basis): __FX_RATES__</div>
  </footer>
</div>

<div class="save-bar" id="saveBar" style="display:none">
  <div class="msg"><span class="count" id="editCount">0</span>件の未保存の編集があります</div>
  <button class="secondary" id="discardBtn">破棄</button>
  <button id="saveBtn">GitHubに保存</button>
</div>

<div class="modal-backdrop hidden" id="sectorModal">
  <div class="modal">
    <h3>セクターラベルを追加</h3>
    <div class="target" id="modalTarget"></div>
    <label for="newSectorInput">新しいラベル</label>
    <input type="text" id="newSectorInput" placeholder="例: AI Core, Watching, 2024Q4..." autocomplete="off">
    <label style="margin-top:14px">既存のラベルから選ぶ</label>
    <div class="existing" id="modalExisting"></div>
    <div class="actions">
      <button class="secondary" id="modalCancel">キャンセル</button>
      <button class="primary" id="modalAdd">追加</button>
    </div>
  </div>
</div>

<script id="__data__" type="application/json">/*__DATA__*/</script>
<script>
const DATA = JSON.parse(document.getElementById("__data__").textContent);
let filter = "all";
let query = "";
let sortKey = "chg_pct";
let sortDir = -1; // -1 desc, 1 asc
let sectorFilter = null; // active sector chip; null = no filter
let sectorBarExpanded = false; // collapsed by default
let editedTickers = null; // ユーザー編集状態 ({symbol -> {sectors, note}})、未編集なら null

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
// Market cap formatter: returns native + USD pair for tooltip display
const fmtMcap = (n) => {
  if (!n) return "—";
  if (n >= 1e12) return (n/1e12).toFixed(2) + "T";
  if (n >= 1e9)  return (n/1e9).toFixed(2) + "B";
  if (n >= 1e6)  return (n/1e6).toFixed(0) + "M";
  return n.toFixed(0);
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
  if (sectorFilter) rs = rs.filter(r => getSectors(r.symbol).includes(sectorFilter));
  if (query) {
    const q = query.toLowerCase();
    rs = rs.filter(r =>
      r.symbol.toLowerCase().includes(q) ||
      r.name.toLowerCase().includes(q) ||
      getSectors(r.symbol).some(s => s.toLowerCase().includes(q)) ||
      getNote(r.symbol).toLowerCase().includes(q)
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
      <div class="val ${cls(top?.chg_pct)}" title="${top?.symbol||''}">
        ${top ? `<span style="font-size:14px;font-weight:500">${top.name}</span><br><span style="font-size:18px">${sign(top.chg_pct)}${fmtNum(top.chg_pct,2)}%</span>` : '—'}
      </div>
    </div>
    <div class="stat">
      <div class="label">Worst</div>
      <div class="val ${cls(bot?.chg_pct)}" title="${bot?.symbol||''}">
        ${bot ? `<span style="font-size:14px;font-weight:500">${bot.name}</span><br><span style="font-size:18px">${sign(bot.chg_pct)}${fmtNum(bot.chg_pct,2)}%</span>` : '—'}
      </div>
    </div>
  `;
}

function renderTable() {
  const rs = rowsFiltered();
  const cols = [
    { k: "symbol",  label: "Symbol",  left: true },
    { k: "name",    label: "Name",    left: true },
    { k: "market",  label: "Mkt",     left: true },
    { k: "last",    label: "Last" },
    { k: "chg_pct", label: "Day %" },
    { k: "chg_5d",  label: "5D %" },
    { k: "chg_1mo", label: "1M %",   extraClass: "col-1mo" },
    { k: "market_cap_usd", label: "Mkt Cap (USD)", extraClass: "col-mcap" },
    { k: "pe_ratio", label: "PER", extraClass: "col-per" },
    { k: "volume",  label: "Volume", extraClass: "col-vol" },
    { k: "range_pos", label: "52W", extraClass: "col-range" },
    { k: "_sectors", label: "Sectors", left: true, extraClass: "col-sectors", sortable: false },
    { k: "note", label: "Note", left: true, extraClass: "col-note", sortable: false },
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
    const dataK = c.sortable === false ? "" : `data-k="${c.k}"`;
    const cursor = c.sortable === false ? ' style="cursor: default"' : "";
    return `<th class="${c.left ? 'left' : ''} ${c.extraClass||''} ${sortCls}" ${dataK}${cursor}>${c.label}${c.sortable === false ? '' : '<span class="arrow">▼</span>'}</th>`;
  }).join("")}</tr></thead>`;

  const tbody = `<tbody>${rs.map(r => {
    const trCls = r.symbol === topSym ? "top-mover" : r.symbol === botSym ? "worst-mover" : "";
    const tickLocal = tsAt(r.tick_time, MARKET_INFO[r.market]?.tz);
    const tickJst = tsAt(r.tick_time, "Asia/Tokyo");
    const tooltip = `Last tick: ${tickLocal} ${MARKET_INFO[r.market]?.label || ''} (${tickJst} JST)`;
    // Market cap display: USD primary (统一通貨), native currency as tooltip
    let mcapDisp = "—", mcapTip = "";
    if (r.market_cap_usd) {
      mcapDisp = "$" + fmtMcap(r.market_cap_usd);
      if (r.market_cap && r.currency !== "$") {
        mcapTip = `${r.currency}${fmtMcap(r.market_cap)} (native)`;
      }
    }
    // Use edited state for sectors and note
    const sectors = getSectors(r.symbol);
    const noteText = getNote(r.symbol);
    const sectorsHtml = `<div class="chips">
      ${sectors.map(s => {
        const active = s === sectorFilter ? " active" : "";
        return `<span class="chip${active}" data-sector="${escapeHtml(s)}" data-sym="${r.symbol}">${escapeHtml(s)}<span class="chip-remove" data-remove="1" data-sym="${r.symbol}" data-s="${escapeHtml(s)}">×</span></span>`;
      }).join("")}
      <span class="add-chip" data-add-sym="${r.symbol}">+ add</span>
    </div>`;
    return `
    <tr class="${trCls}">
      <td class="left sym">${r.symbol}</td>
      <td class="left name" title="${escapeHtml(r.name)}">${escapeHtml(r.name)}</td>
      <td class="left market">${r.market}</td>
      <td title="${tooltip}">${fmtPrice(r.last, r.currency)}</td>
      <td><span class="pct ${cls(r.chg_pct)}">${sign(r.chg_pct)}${fmtNum(r.chg_pct,2)}%</span></td>
      <td class="pct-soft ${cls(r.chg_5d)}">${sign(r.chg_5d)}${fmtNum(r.chg_5d,1)}%</td>
      <td class="pct-soft ${cls(r.chg_1mo)} chg1mo">${sign(r.chg_1mo)}${fmtNum(r.chg_1mo,1)}%</td>
      <td class="mcap" title="${mcapTip}">${mcapDisp}</td>
      <td class="per">${r.pe_ratio != null ? fmtNum(r.pe_ratio, 1) : "—"}</td>
      <td>${fmtVol(r.volume)}</td>
      <td class="range-cell"><div class="range" style="--p:${(r.range_pos||50).toFixed(0)}%" title="${fmtNum(r.range_pos,0)}% of 52w range"></div></td>
      <td class="left sectors-cell">${sectorsHtml}</td>
      <td class="left note-cell" data-sym="${r.symbol}"><span class="note-display">${escapeHtml(noteText)}</span></td>
    </tr>`;
  }).join("")}</tbody>`;

  document.getElementById("tableMount").innerHTML = `<table>${thead}${tbody}</table>`;

  // Sortable column headers
  document.querySelectorAll("th[data-k]").forEach(th => {
    th.addEventListener("click", () => {
      const k = th.dataset.k;
      if (k === sortKey) sortDir = -sortDir; else { sortKey = k; sortDir = -1; }
      renderTable();
    });
  });
  // Sector chip click: filter by sector (but not on remove button)
  document.querySelectorAll("td.sectors-cell .chip").forEach(chip => {
    chip.addEventListener("click", (e) => {
      if (e.target.closest(".chip-remove")) return;
      const s = chip.dataset.sector;
      sectorFilter = (sectorFilter === s) ? null : s;
      renderSectorBar();
      rerender();
    });
  });
  // Remove chip click (×)
  document.querySelectorAll(".chip-remove").forEach(rm => {
    rm.addEventListener("click", (e) => {
      e.stopPropagation();
      const sym = rm.dataset.sym;
      const s = rm.dataset.s;
      const current = getSectors(sym);
      setSectors(sym, current.filter(x => x !== s));
      rerender();
    });
  });
  // Add chip click → open modal
  document.querySelectorAll(".add-chip").forEach(btn => {
    btn.addEventListener("click", () => openSectorModal(btn.dataset.addSym));
  });
  // Note cell click → enter edit mode
  document.querySelectorAll("td.note-cell").forEach(cell => {
    cell.addEventListener("click", (e) => {
      if (cell.querySelector("input")) return;
      const sym = cell.dataset.sym;
      const current = getNote(sym);
      cell.innerHTML = `<input type="text" class="note-input" maxlength="200" value="${escapeHtml(current)}" placeholder="メモを入力...">`;
      const input = cell.querySelector("input");
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
      const commit = () => {
        const newVal = input.value;
        if (newVal !== current) setNote(sym, newVal);
        cell.innerHTML = `<span class="note-display">${escapeHtml(newVal)}</span>`;
      };
      input.addEventListener("blur", commit);
      input.addEventListener("keydown", (ke) => {
        if (ke.key === "Enter") { ke.preventDefault(); input.blur(); }
        if (ke.key === "Escape") { input.value = current; input.blur(); }
      });
    });
  });

  // Also render mobile cards (CSS controls which one is visible)
  renderMobileCards(rs, topSym, botSym);
}

// Render mobile card layout
function renderMobileCards(rs, topSym, botSym) {
  const html = rs.map(r => {
    const trCls = r.symbol === topSym ? " top-mover" : r.symbol === botSym ? " worst-mover" : "";
    const sectors = getSectors(r.symbol);
    const noteText = getNote(r.symbol);
    const mcapUsd = r.market_cap_usd ? "$" + fmtMcap(r.market_cap_usd) : "—";
    const sectorsHtml = `<div class="chips">
      ${sectors.map(s => {
        const active = s === sectorFilter ? " active" : "";
        return `<span class="chip${active}" data-sector="${escapeHtml(s)}" data-sym-m="${r.symbol}">${escapeHtml(s)}<span class="chip-remove" data-remove-m="1" data-sym-m="${r.symbol}" data-s-m="${escapeHtml(s)}">×</span></span>`;
      }).join("")}
      <span class="add-chip" data-add-sym-m="${r.symbol}">+ add</span>
    </div>`;
    return `
    <div class="mcard${trCls}" data-sym="${r.symbol}">
      <div class="mcard-top">
        <div>
          <div class="mcard-name" title="${escapeHtml(r.name)}">${escapeHtml(r.name)}</div>
          <div class="mcard-sym">${r.symbol}<span class="mcard-mkt">${r.market}</span></div>
        </div>
        <div class="mcard-pct">
          <div class="pct-big ${cls(r.chg_pct)}">${sign(r.chg_pct)}${fmtNum(r.chg_pct,2)}%</div>
          <div class="price">${fmtPrice(r.last, r.currency)}</div>
        </div>
      </div>
      <div class="mcard-meta">
        <div class="mc">
          <div class="mc-label">5D</div>
          <div class="mc-val ${cls(r.chg_5d)}">${sign(r.chg_5d)}${fmtNum(r.chg_5d,1)}%</div>
        </div>
        <div class="mc">
          <div class="mc-label">1M</div>
          <div class="mc-val ${cls(r.chg_1mo)}">${sign(r.chg_1mo)}${fmtNum(r.chg_1mo,1)}%</div>
        </div>
        <div class="mc">
          <div class="mc-label">PER</div>
          <div class="mc-val">${r.pe_ratio != null ? fmtNum(r.pe_ratio, 1) : "—"}</div>
        </div>
        <div class="mc">
          <div class="mc-label">Mkt Cap</div>
          <div class="mc-val">${mcapUsd}</div>
        </div>
      </div>
      <div class="mcard-sectors">${sectorsHtml}</div>
      <div class="mcard-note${noteText ? '' : ' empty'}" data-sym-note="${r.symbol}">${escapeHtml(noteText)}</div>
    </div>`;
  }).join("");
  document.getElementById("mobileCards").innerHTML = html;

  // Wire up sector chip click in mobile
  document.querySelectorAll(".mcard .chip[data-sector]").forEach(chip => {
    chip.addEventListener("click", (e) => {
      if (e.target.closest(".chip-remove")) return;
      const s = chip.dataset.sector;
      sectorFilter = (sectorFilter === s) ? null : s;
      renderSectorBar();
      rerender();
    });
  });
  document.querySelectorAll(".mcard .chip-remove").forEach(rm => {
    rm.addEventListener("click", (e) => {
      e.stopPropagation();
      const sym = rm.dataset.symM;
      const s = rm.dataset.sM;
      const current = getSectors(sym);
      setSectors(sym, current.filter(x => x !== s));
      rerender();
    });
  });
  document.querySelectorAll(".mcard .add-chip").forEach(btn => {
    btn.addEventListener("click", () => openSectorModal(btn.dataset.addSymM));
  });
  // Mobile note edit
  document.querySelectorAll(".mcard-note").forEach(noteEl => {
    noteEl.addEventListener("click", () => {
      if (noteEl.querySelector("input")) return;
      const sym = noteEl.dataset.symNote;
      const current = getNote(sym);
      noteEl.classList.remove("empty");
      noteEl.innerHTML = `<input type="text" maxlength="200" value="${escapeHtml(current)}" placeholder="メモを入力...">`;
      const input = noteEl.querySelector("input");
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
      const commit = () => {
        const newVal = input.value;
        if (newVal !== current) setNote(sym, newVal);
        noteEl.classList.toggle("empty", !newVal);
        noteEl.innerHTML = escapeHtml(newVal);
      };
      input.addEventListener("blur", commit);
      input.addEventListener("keydown", (ke) => {
        if (ke.key === "Enter") { ke.preventDefault(); input.blur(); }
        if (ke.key === "Escape") { input.value = current; input.blur(); }
      });
    });
  });
}

// HTML escape helper
function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function rerender() { renderSummary(); renderTable(); }

// Render the sector filter bar above the table (collapsed by default)
function renderSectorBar() {
  // Aggregate all unique sectors with counts (using edited state if available)
  const counts = new Map();
  for (const r of DATA) {
    const sectors = getSectors(r.symbol);
    for (const s of sectors) {
      counts.set(s, (counts.get(s) || 0) + 1);
    }
  }
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));

  const toggleClass = sectorBarExpanded ? "open" : "";
  const panelClass = sectorBarExpanded ? "" : "hidden";

  const html = `
    <div class="toggle-row">
      <button class="toggle-btn ${toggleClass}" id="sectorToggle">
        Filter by sector <span class="arrow">▼</span>
      </button>
      ${sectorFilter ? `
        <span class="active-chip">
          ${escapeHtml(sectorFilter)}
          <span class="close" id="clearActiveChip">×</span>
        </span>
      ` : ''}
      <span style="margin-left:auto;font:500 10px/1 'IBM Plex Mono',monospace;color:var(--ink-faint);letter-spacing:0.06em;text-transform:uppercase">${sorted.length} sectors / ${DATA.length} tickers</span>
    </div>
    <div class="panel ${panelClass}" id="sectorPanel">
      ${sorted.map(([s, n]) => {
        const active = s === sectorFilter ? " active" : "";
        return `<span class="chip${active}" data-sector="${escapeHtml(s)}">${escapeHtml(s)} <span style="opacity:0.6;font-size:9px">${n}</span></span>`;
      }).join("")}
    </div>
  `;
  document.getElementById("sectorBar").innerHTML = html;

  // Toggle expansion
  document.getElementById("sectorToggle").addEventListener("click", () => {
    sectorBarExpanded = !sectorBarExpanded;
    renderSectorBar();
  });
  // Chip click → set as filter
  document.querySelectorAll("#sectorBar .panel .chip").forEach(chip => {
    chip.addEventListener("click", () => {
      const s = chip.dataset.sector;
      sectorFilter = (sectorFilter === s) ? null : s;
      renderSectorBar();
      rerender();
    });
  });
  // Active chip close button
  const close = document.getElementById("clearActiveChip");
  if (close) close.addEventListener("click", () => {
    sectorFilter = null;
    renderSectorBar();
    rerender();
  });
}

// Edit helpers - retrieve current state for a ticker (edited > original)
function getSectors(symbol) {
  if (editedTickers && editedTickers[symbol] && editedTickers[symbol].sectors !== undefined) {
    return editedTickers[symbol].sectors;
  }
  const row = DATA.find(r => r.symbol === symbol);
  return row?.sectors || [];
}
function getNote(symbol) {
  if (editedTickers && editedTickers[symbol] && editedTickers[symbol].note !== undefined) {
    return editedTickers[symbol].note;
  }
  const row = DATA.find(r => r.symbol === symbol);
  return row?.note || "";
}
function setSectors(symbol, sectors) {
  editedTickers = editedTickers || {};
  editedTickers[symbol] = editedTickers[symbol] || {};
  editedTickers[symbol].sectors = sectors;
  renderSaveBar();
}
function setNote(symbol, note) {
  editedTickers = editedTickers || {};
  editedTickers[symbol] = editedTickers[symbol] || {};
  editedTickers[symbol].note = note;
  renderSaveBar();
}
function editCount() {
  if (!editedTickers) return 0;
  return Object.keys(editedTickers).length;
}
function renderSaveBar() {
  const bar = document.getElementById("saveBar");
  const n = editCount();
  if (n === 0) {
    bar.style.display = "none";
  } else {
    bar.style.display = "flex";
    document.getElementById("editCount").textContent = n;
  }
}

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

// === Sector add modal ===
let modalCurrentSym = null;
function openSectorModal(symbol) {
  modalCurrentSym = symbol;
  const row = DATA.find(r => r.symbol === symbol);
  document.getElementById("modalTarget").textContent = `${row.name} (${symbol})`;
  document.getElementById("newSectorInput").value = "";
  // Build list of existing sectors not yet on this ticker
  const allSectors = new Set();
  for (const r of DATA) for (const s of getSectors(r.symbol)) allSectors.add(s);
  const currentSectors = new Set(getSectors(symbol));
  const available = [...allSectors].filter(s => !currentSectors.has(s)).sort();
  document.getElementById("modalExisting").innerHTML = available.length
    ? available.map(s => `<span class="chip" data-pick="${escapeHtml(s)}">${escapeHtml(s)}</span>`).join("")
    : '<span style="color:var(--ink-faint);font-size:11px;font-style:italic">既存ラベルなし</span>';
  document.querySelectorAll("#modalExisting .chip").forEach(chip => {
    chip.addEventListener("click", () => {
      addSectorToCurrent(chip.dataset.pick);
    });
  });
  document.getElementById("sectorModal").classList.remove("hidden");
  setTimeout(() => document.getElementById("newSectorInput").focus(), 50);
}
function addSectorToCurrent(label) {
  if (!modalCurrentSym || !label) return;
  label = label.trim();
  if (!label) return;
  const current = getSectors(modalCurrentSym);
  if (current.includes(label)) {
    closeSectorModal();
    return;
  }
  setSectors(modalCurrentSym, [...current, label]);
  closeSectorModal();
  rerender();
  renderSectorBar();
}
function closeSectorModal() {
  document.getElementById("sectorModal").classList.add("hidden");
  modalCurrentSym = null;
}

// === Save: serialize edits and copy/open GitHub ===
function buildUpdatedJson() {
  // Reconstruct tickers.json structure with edits applied
  const result = DATA.map(r => ({
    symbol: r.symbol,
    name: r.name,
    sectors: getSectors(r.symbol),
    note: getNote(r.symbol),
  }));
  return JSON.stringify(result, null, 2);
}
function saveToGitHub() {
  const json = buildUpdatedJson();
  // GitHub repo URL detection: assume the page is hosted at <user>.github.io/<repo>/
  const host = location.host; // e.g. "ishii-39.github.io"
  const pathParts = location.pathname.split("/").filter(Boolean);
  let editUrl = "https://github.com/";
  if (host.endsWith(".github.io") && pathParts.length > 0) {
    const user = host.split(".")[0];
    const repo = pathParts[0];
    editUrl = `https://github.com/${user}/${repo}/edit/main/tickers.json`;
  } else {
    editUrl = "https://github.com";
  }
  // Copy to clipboard
  copyToClipboard(json).then(() => {
    showToast("✓ tickers.json の内容をクリップボードにコピーしました。新タブで GitHub の編集画面が開きます。\n\n1) 全選択 (Cmd+A) → 2) ペースト (Cmd+V) → 3) 下にスクロール → 4) Commit changes");
    window.open(editUrl, "_blank");
  }).catch(() => {
    // Fallback: show JSON in a prompt
    const ta = document.createElement("textarea");
    ta.value = json;
    ta.style.cssText = "position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:80vw;height:60vh;z-index:300;padding:12px;font-family:monospace;font-size:11px;background:white;border:2px solid black";
    document.body.appendChild(ta);
    ta.select();
    showToast("コピーできなかった場合: 表示されたテキストエリアの内容を手動でコピーして GitHub に貼り付けてください。");
  });
}
function copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text);
  }
  return new Promise((resolve, reject) => {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;top:-1000px";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
      document.body.removeChild(ta);
      resolve();
    } catch (e) {
      document.body.removeChild(ta);
      reject(e);
    }
  });
}
function showToast(msg) {
  const t = document.createElement("div");
  t.textContent = msg;
  t.style.cssText = "position:fixed;top:20px;left:50%;transform:translateX(-50%);background:var(--ink);color:var(--bg);padding:12px 18px;font:500 12px/1.5 'IBM Plex Sans',sans-serif;max-width:80vw;z-index:300;white-space:pre-line;box-shadow:0 4px 14px rgba(0,0,0,0.25)";
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 6000);
}
function discardEdits() {
  if (!confirm("未保存の編集を全て破棄します。よろしいですか？")) return;
  editedTickers = null;
  renderSaveBar();
  rerender();
  renderSectorBar();
}

// === Boot ===
renderTabs();
rerender();
renderSectorBar();
renderMarketStatus();
// Re-render market status banner every minute so Open/Closed labels stay current
setInterval(renderMarketStatus, 60_000);

// Modal events
document.getElementById("modalCancel").addEventListener("click", closeSectorModal);
document.getElementById("modalAdd").addEventListener("click", () => {
  const val = document.getElementById("newSectorInput").value.trim();
  if (val) addSectorToCurrent(val);
});
document.getElementById("newSectorInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    const val = e.target.value.trim();
    if (val) addSectorToCurrent(val);
  }
  if (e.key === "Escape") closeSectorModal();
});
document.getElementById("sectorModal").addEventListener("click", (e) => {
  if (e.target.id === "sectorModal") closeSectorModal();
});
// Save bar events
document.getElementById("saveBtn").addEventListener("click", saveToGitHub);
document.getElementById("discardBtn").addEventListener("click", discardEdits);
// Warn on navigation if unsaved edits
window.addEventListener("beforeunload", (e) => {
  if (editCount() > 0) {
    e.preventDefault();
    e.returnValue = "";
  }
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
