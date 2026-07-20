# -*- coding: utf-8 -*-
"""
top_holdings.py
================
テック・バリュー/景気循環・ディフェンシブの3カテゴリについて、
現在の代表銘柄(上位10銘柄)を1枚の画像として可視化します。

カテゴリの構成ETFは sector_rotation_monitor.py の CONFIG["pairs"] と
揃えています (value_tech の流入側 = バリュー、defensive_tech の
流入側/流出側 = ディフェンシブ/テック)。

各ETFの上位保有銘柄は yfinance の funds_data.top_holdings から取得します
(通常上位10銘柄程度)。カテゴリが複数ETFで構成される場合は、
ETFごとの保有比率を均等加重平均し、その値で再ランキングします
(価格バスケットを等ウェイト平均する既存ロジックと考え方を統一)。

制約: 各ETFについて取得できるのは概ね上位10銘柄までのため、
あるETFでは上位に入らない(≒比率0として扱われる)銘柄がある点に注意してください。
厳密な保有比率ではなく「現在の代表的な物色対象」を把握するための参考情報です。

実行方法:
    python top_holdings.py

出力:
    ./output/06_sector_rotation_top_holdings_YYYYMMDD.png
"""

import console_utf8  # noqa: F401  (文字化け対策。最初にimportする)

import os
import sys
import time
from datetime import datetime

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

try:
    import yfinance as yf
except ImportError:
    print("[致命的エラー] yfinance がインストールされていません。")
    print("  対処: pip install yfinance を実行してください。")
    sys.exit(1)

from sector_rotation_monitor import CONFIG

OUTPUT_DIR = CONFIG["output_dir"]
TOP_N = 10
MAX_RETRIES = 3
RETRY_WAIT_SEC = 5

# --- 日本語フォントの自動選択 (文字化け対策) ---
_JP_FONT_CANDIDATES = ["Yu Gothic", "Meiryo", "MS Gothic", "Noto Sans CJK JP"]
_available = {f.name for f in fm.fontManager.ttflist}
for _name in _JP_FONT_CANDIDATES:
    if _name in _available:
        plt.rcParams["font.family"] = _name
        break
plt.rcParams["axes.unicode_minus"] = False


def _find_pair(name: str) -> dict:
    return next(p for p in CONFIG["pairs"] if p["name"] == name)


# --- カテゴリ定義 (既存のバスケット構成と揃える) ---
_value_pair = _find_pair("value_tech")
_defensive_pair = _find_pair("defensive_tech")

CATEGORIES = [
    {"label": "①バリュー/景気循環", "tickers": _value_pair["inflow_tickers"], "color": "#2E5EAA"},
    {"label": "②ディフェンシブ", "tickers": _defensive_pair["inflow_tickers"], "color": "#2E8B4F"},
    {"label": "③テック", "tickers": _defensive_pair["outflow_tickers"], "color": "#B23A48"},
]


def fetch_top_holdings(ticker: str) -> pd.DataFrame:
    """
    指定ETFの上位保有銘柄 (Symbol, Name, Holding Percent) を取得する。
    失敗時はリトライし、最終的に空DataFrameを返して処理は継続させる。
    """
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            holdings = yf.Ticker(ticker).funds_data.top_holdings
            if holdings is None or holdings.empty:
                raise ValueError("top_holdings が空でした")
            df = holdings.reset_index()
            df.columns = ["Symbol", "Name", "Weight"]
            return df
        except Exception as e:
            last_err = e
            print(f"[警告] {ticker} の保有銘柄取得に失敗 (試行 {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_WAIT_SEC)
    print(f"[警告] [エラー発生源: fetch_top_holdings({ticker})] "
          f"{MAX_RETRIES}回の試行後も取得できませんでした。最終エラー: {last_err}")
    return pd.DataFrame(columns=["Symbol", "Name", "Weight"])


def aggregate_category(tickers: list) -> pd.DataFrame:
    """
    カテゴリを構成する複数ETFの上位保有銘柄を、ETF間で均等加重平均し、
    上位 TOP_N 銘柄に絞ったDataFrame (Symbol, Name, Weight) を返す。
    """
    per_etf = {t: fetch_top_holdings(t) for t in tickers}
    valid = {t: df for t, df in per_etf.items() if not df.empty}
    if not valid:
        raise RuntimeError(
            f"[エラー発生源: aggregate_category] 構成ETF {tickers} のいずれからも"
            " 保有銘柄を取得できませんでした。"
        )
    if len(valid) < len(tickers):
        missing = set(tickers) - set(valid)
        print(f"[警告] 以下のETFは保有銘柄が取得できず、集計から除外します: {sorted(missing)}")

    names = {}
    weight_sum = {}
    for t, df in valid.items():
        for _, row in df.iterrows():
            sym = row["Symbol"]
            names.setdefault(sym, row["Name"])
            weight_sum[sym] = weight_sum.get(sym, 0.0) + float(row["Weight"])

    n_etfs = len(valid)
    records = [
        {"Symbol": sym, "Name": names[sym], "Weight": w / n_etfs}
        for sym, w in weight_sum.items()
    ]
    result = pd.DataFrame(records).sort_values("Weight", ascending=False).head(TOP_N)
    return result.reset_index(drop=True)


def plot_categories(category_data: list, stamp: str) -> str:
    fig, axes = plt.subplots(1, len(category_data), figsize=(6.2 * len(category_data), 7.5))
    if len(category_data) == 1:
        axes = [axes]

    for ax, cat in zip(axes, category_data):
        df = cat["holdings"].iloc[::-1]  # 上位が上に来るよう反転(barhは下から描画されるため)
        y_pos = range(len(df))
        ax.barh(y_pos, df["Weight"] * 100, color=cat["color"], height=0.65)

        for y, (_, row) in zip(y_pos, df.iterrows()):
            ax.text(row["Weight"] * 100 + 0.3, y, f"{row['Name']}  ({row['Weight']*100:.1f}%)",
                    va="center", fontsize=8.5, color="#333333")

        ax.set_yticks(list(y_pos))
        ax.set_yticklabels(df["Symbol"], fontsize=10, weight="bold")
        ax.set_xlim(0, max(df["Weight"] * 100) * 1.9)
        ax.set_title(f"{cat['label']}\n({'+'.join(cat['tickers'])})", fontsize=13, color=cat["color"], loc="left")
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.tick_params(left=False)
        ax.grid(True, axis="x", color="#EEEEEE", lw=0.6)

    fig.suptitle(f"米国株 カテゴリ別 代表銘柄 (上位{TOP_N}銘柄) — データ基準日: {stamp}", fontsize=15, y=1.02)
    fig.text(0.0, -0.03,
             "※ 各ETFの上位保有銘柄(概ね上位10銘柄)をETF間で均等加重平均して再ランキングしたものです。"
             "厳密な保有比率ではなく、現在の代表的な物色対象の参考情報としてご利用ください。",
             fontsize=8, color="#777777")

    save_path = os.path.join(OUTPUT_DIR, f"06_sector_rotation_top_holdings_{stamp}.png")
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return save_path


def main():
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")

        category_data = []
        for cat in CATEGORIES:
            print(f"[情報] {cat['label']} ({'+'.join(cat['tickers'])}) の保有銘柄を取得中...")
            holdings = aggregate_category(cat["tickers"])
            category_data.append({**cat, "holdings": holdings})
            top3 = ", ".join(f"{r.Symbol}({r.Weight*100:.1f}%)" for r in holdings.head(3).itertuples())
            print(f"  上位3銘柄: {top3}")

        save_path = plot_categories(category_data, stamp)
        print(f"\n[完了] 代表銘柄画像を保存しました: {save_path}")

    except Exception as e:
        print(f"\n[エラー] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
