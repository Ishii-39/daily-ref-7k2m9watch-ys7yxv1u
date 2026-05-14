# Daily Watchlist

93銘柄（日本・米国・台湾・韓国・中国・香港の半導体／AI／データセンター関連株）の前日比%を、yfinance で自動取得して HTML 一覧表示する個人向けダッシュボード。**完全無料**。

## クイックスタート

### 方法A: ローカル実行（30秒で実値を確認）

```bash
./setup.sh
```

これだけです。venv 作成 → yfinance install → 93銘柄取得 → `index.html` 生成 → ブラウザで開く、まで自動でやります。

実行後、銘柄を編集したい場合は `tickers.json` を編集して再度 `./setup.sh`。

### 方法B: GitHub Pages にデプロイして15分毎に自動更新

```bash
# repo 初期化
git init -b main
git add .
git commit -m "init: daily watchlist"

# GitHub に repo を作成し、URL を設定して push
git remote add origin git@github.com:<user>/<repo>.git
git push -u origin main
```

GitHub UI で:
1. Settings → **Pages** → Source = `main` branch / `/ (root)`
2. Settings → **Actions** → "General" → Workflow permissions = **Read and write permissions**

これで完了。`push` した時点で GHA が即座に走り `index.html` が実データで埋まり、以降は **市場時間中15分毎に自動更新**。`https://<user>.github.io/<repo>/` でアクセス可能。

## アーキテクチャ

```
                push trigger
                    ↓
GitHub Actions  (15分毎 cron + push)
        ↓
fetch_prices.py (yfinance)  ←─── Yahoo Finance
        ↓
index.html を再生成
        ↓
git commit + push
        ↓
GitHub Pages で配信
        ↓
ブラウザが5分毎に自動リロード → 最新コミット反映
```

- yfinance がサーバー側で Yahoo Finance の crumb/cookie を自動解決して価格取得
- アジア5市場 (UTC 0-8) + 米国市場含むアフターアワーズ (UTC 13-23) を15分毎にカバー
- 開いたページは `<meta http-equiv="refresh" content="300">` により5分毎に自動リロード
- 結果として「常に最大 20分前のデータ」(Yahoo 15-20分遅延 + GHA cron 揺らぎ) が表示される

## ファイル構成

```
.
├── fetch_prices.py             # 取得＋HTML生成スクリプト (Python)
├── tickers.json                # 93銘柄のフラットリスト
├── requirements.txt            # yfinance のみ
├── setup.sh                    # ローカル実行用 (chmod +x 済み)
├── README.md                   # この文書
├── index.html                  # 生成物 (setup.sh または GHA が生成)
└── .github/workflows/update.yml  # 自動更新ワークフロー
```

## 各市場のタイムスタンプ表示

ヘッダー直下のバナーで6市場の状態を表示:
- **Open/Closed**: ブラウザ現在時刻 vs 各市場ローカル営業時間でその場算出
- **Last tick 時刻**: 直近の引け時刻をローカル時間 + JST 換算の2行で表示
- 当日でなければ日付付きで明示

個別銘柄の Last 価格セルにホバーすると、その銘柄個別のティック時刻もツールチップで確認可能。

## 銘柄の追加・削除

`tickers.json` を編集。市場と通貨はサフィックスから自動判定:

| サフィックス | 市場 | 通貨 |
|---|---|---|
| `.T` | JP (TSE) | ¥ |
| `.KS` / `.KQ` | KR (KOSPI/KOSDAQ) | ₩ |
| `.SS` / `.SZ` | CN (SSE/SZSE) | ¥ (CNY) |
| `.HK` | HK (HKEX) | HK$ |
| `.TW` / `.TWO` | TW (TWSE/TPEx) | NT$ |
| なし | US (NYSE/Nasdaq) | $ |

編集後、ローカルなら `./setup.sh`、デプロイ済みなら `git push` するだけで反映。

## トラブルシューティング

**Q: `./setup.sh` で `python3 not found`**  
A: macOS なら `brew install python` で。

**Q: yfinance で銘柄が SKIP される**  
A: 一部の中国A株はその日 Yahoo 側でデータが空のケースあり。スクリプトは該当銘柄を `[SKIP]` ログ出力して継続。新規上場（Moore Threads 688795.SS, MetaX 688802.SS）も履歴が短いため 5D% / 1M% が `—` 表示になる場合あり。

**Q: GHA が走らない**  
A: Settings → Actions → "Workflow permissions" が **Read and write** になっているか確認。また初回 push 時、`fetch_prices.py` / `tickers.json` / workflow 自体に変更がないと `push` トリガーは起動しない仕様（schedule cron は確実に動く）。

**Q: もっと鮮度を上げたい**  
A: 現状は無料データの限界（Yahoo 15-20分遅延）。真のリアルタイムには Polygon ($29~/月) や IEX Cloud、または楽天/SBI証券のAPI、J-Quants 有料プランへの移行が必要。Cloudflare Workers プロキシ + Yahoo v8 chart API で30秒粒度のクライアントポーリングは可能だが、根本の遅延は変わらない。

## 注意

- Yahoo Finance の利用規約上、データの**再配布は商用利用不可**。個人利用のみ。
- 中国A株 (`.SS` / `.SZ`) は yfinance での取得が日によって不安定。
