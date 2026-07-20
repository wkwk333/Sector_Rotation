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

各銘柄には割安度の目安として実績PER(trailingPE。取得できない場合は
予想PERで代替)を併記し、同じカテゴリ内の中央値と比較した相対位置
(中央値より安ければ「割安」、高ければ「割高」)を色分け表示します。

制約:
- 各ETFについて取得できるのは概ね上位10銘柄までのため、
  あるETFでは上位に入らない(≒比率0として扱われる)銘柄がある点に注意してください。
  厳密な保有比率ではなく「現在の代表的な物色対象」を把握するための参考情報です。
- 割安度はあくまで「同じカテゴリ内の他銘柄と比べたPERの相対位置」であり、
  成長性・収益性・業種特性の違いを考慮した本来の割安度(フェアバリュー)
  ではありません。参考情報としてご利用ください。
- 赤字企業などPERが算出できない銘柄は「PER: N/A」と表示されます。

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
CHEAP_THRESHOLD_PCT = -10.0   # カテゴリ中央値よりこの%以上安ければ「割安」
EXPENSIVE_THRESHOLD_PCT = 10.0  # カテゴリ中央値よりこの%以上高ければ「割高」

COLOR_CHEAP = "#2E8B4F"      # 割安: 緑
COLOR_EXPENSIVE = "#B23A48"  # 割高: 赤
COLOR_NEUTRAL_VAL = "#8A7A1F"  # 中立: 琥珀寄りのグレー
COLOR_NA = "#999999"         # PER取得不可: グレー

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
# value_tech/defensive_tech は「テック優位ほどレシオが増える」向きで定義されているため
# (inflow側=テック)、バリュー/ディフェンシブ側は outflow_tickers を参照する。
_value_pair = _find_pair("value_tech")
_defensive_pair = _find_pair("defensive_tech")

CATEGORIES = [
    {"label": "①バリュー/景気循環", "tickers": _value_pair["outflow_tickers"], "color": "#2E5EAA"},
    {"label": "②ディフェンシブ", "tickers": _defensive_pair["outflow_tickers"], "color": "#2E8B4F"},
    {"label": "③テック", "tickers": _defensive_pair["inflow_tickers"], "color": "#B23A48"},
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


def fetch_pe(symbol: str):
    """
    銘柄の実績PER(trailingPE)を取得する。取得できなければ予想PER(forwardPE)で
    代替する。どちらも無ければ None を返す(赤字企業など)。
    """
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            info = yf.Ticker(symbol).get_info()
            pe = info.get("trailingPE")
            if pe is None or pe <= 0:
                pe = info.get("forwardPE")
            if pe is not None and pe <= 0:
                pe = None
            return pe
        except Exception as e:
            last_err = e
            print(f"[警告] {symbol} のPER取得に失敗 (試行 {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_WAIT_SEC)
    print(f"[警告] [エラー発生源: fetch_pe({symbol})] "
          f"{MAX_RETRIES}回の試行後も取得できませんでした。最終エラー: {last_err}")
    return None


def add_valuation(df: pd.DataFrame) -> pd.DataFrame:
    """
    各銘柄にPERと、カテゴリ内中央値との相対評価(割安/割高/中立/N/A)を付与する。
    """
    df = df.copy()
    df["PE"] = [fetch_pe(sym) for sym in df["Symbol"]]

    valid_pe = df["PE"].dropna()
    median_pe = valid_pe.median() if not valid_pe.empty else None

    def classify(pe):
        if pe is None or pd.isna(pe) or median_pe is None:
            return None, "PER: N/A", COLOR_NA
        rel_pct = (pe - median_pe) / median_pe * 100
        if rel_pct <= CHEAP_THRESHOLD_PCT:
            return rel_pct, f"PER {pe:.1f}倍 (割安・中央値比{rel_pct:+.0f}%)", COLOR_CHEAP
        if rel_pct >= EXPENSIVE_THRESHOLD_PCT:
            return rel_pct, f"PER {pe:.1f}倍 (割高・中央値比{rel_pct:+.0f}%)", COLOR_EXPENSIVE
        return rel_pct, f"PER {pe:.1f}倍 (中立・中央値比{rel_pct:+.0f}%)", COLOR_NEUTRAL_VAL

    classified = [classify(pe) for pe in df["PE"]]
    df["RelPct"] = [c[0] for c in classified]
    df["ValuationText"] = [c[1] for c in classified]
    df["ValuationColor"] = [c[2] for c in classified]
    return df, median_pe


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
            ax.text(row["Weight"] * 100 + 0.3, y + 0.16, f"{row['Name']}  ({row['Weight']*100:.1f}%)",
                    va="center", fontsize=8.5, color="#333333")
            ax.text(row["Weight"] * 100 + 0.3, y - 0.20, row["ValuationText"],
                    va="center", fontsize=7.8, color=row["ValuationColor"], weight="bold")

        ax.set_yticks(list(y_pos))
        ax.set_yticklabels(df["Symbol"], fontsize=10, weight="bold")
        ax.set_xlim(0, max(df["Weight"] * 100) * 2.1)
        median_pe = cat.get("median_pe")
        median_text = f"category PER中央値: {median_pe:.1f}倍" if median_pe else "category PER中央値: 算出不可"
        ax.set_title(f"{cat['label']}\n({'+'.join(cat['tickers'])})  {median_text}",
                     fontsize=12.5, color=cat["color"], loc="left")
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.tick_params(left=False)
        ax.grid(True, axis="x", color="#EEEEEE", lw=0.6)

    fig.suptitle(f"米国株 カテゴリ別 代表銘柄 (上位{TOP_N}銘柄) — データ基準日: {stamp}", fontsize=15, y=1.03)
    fig.text(0.0, -0.05,
             "※ 保有比率は各ETFの上位保有銘柄(概ね上位10銘柄)をETF間で均等加重平均して再ランキングしたものです。\n"
             "※ 割安度は実績PER(取得不可の場合は予想PER)をカテゴリ内中央値と比較した相対位置であり、"
             "成長性等を考慮した本来のフェアバリューではありません。緑=割安 / 琥珀=中立 / 赤=割高 / 灰=PER取得不可。",
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
            print(f"  PERを取得中... ({', '.join(holdings['Symbol'])})")
            holdings, median_pe = add_valuation(holdings)
            category_data.append({**cat, "holdings": holdings, "median_pe": median_pe})
            top3 = ", ".join(f"{r.Symbol}({r.Weight*100:.1f}%, {r.ValuationText})"
                             for r in holdings.head(3).itertuples())
            print(f"  上位3銘柄: {top3}")

        save_path = plot_categories(category_data, stamp)
        print(f"\n[完了] 代表銘柄画像を保存しました: {save_path}")

    except Exception as e:
        print(f"\n[エラー] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
