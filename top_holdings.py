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

各銘柄の割安度は、単一指標(PERのみ)の弱点(成長性を無視する、赤字企業で
使えない、資本構成の違いを無視する等)を補うため、以下5指標を組み合わせた
「総合割安度スコア」で判定します (VALUATION_METRICS 参照)。

  - PER (実績優先、無ければ予想)        … 低いほど割安
  - PEGレシオ (PER ÷ 利益成長率)        … 低いほど割安。成長性で正規化
  - PBR (株価純資産倍率)                … 低いほど割安。金融・資本財向け
  - EV/EBITDA                           … 低いほど割安。資本構成の歪みを除去
  - 配当利回り                          … 高いほど割安 (株主還元の厚さ)
  - FCF利回り (FCF ÷ 時価総額)          … 高いほど割安。会計操作されにくい

各指標をカテゴリ内でパーセンタイル順位化し(スケールの違う指標を単純平均
できないため)、「安い方」を100点・「高い方」を0点として指標間で平均した
ものが総合割安度スコアです。データが取得できない指標は平均から除外され、
使用できた指標数を併記します。スコア66.7点以上=割安、33.3点以下=割高、
その他=中立と判定します。

制約:
- 各ETFについて取得できるのは概ね上位10銘柄までのため、
  あるETFでは上位に入らない(≒比率0として扱われる)銘柄がある点に注意してください。
  厳密な保有比率ではなく「現在の代表的な物色対象」を把握するための参考情報です。
- あくまで「同じカテゴリ内の他銘柄と比べた相対位置」であり、本来のフェア
  バリュー(理論株価)ではありません。参考情報としてご利用ください。
- PBR がマイナス(債務超過等で株主資本がマイナス)、PER/PEG/EV-EBITDA が
  マイナスの銘柄は、その指標を「割安判定不能」として除外します
  (マイナス値は小さいほど割安、という単純比較ができないため)。

実行方法:
    python top_holdings.py

出力:
    ./output/06_sector_rotation_top_holdings_YYYYMMDD.png         (PC/exe向け、横並び)
    ./output/06_sector_rotation_top_holdings_mobile_YYYYMMDD.png  (Androidアプリ向け、縦積み)

どちらも内容は同一で、レイアウトのみが異なります。
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
CHEAP_SCORE_THRESHOLD = 66.7    # 総合割安度スコアがこれ以上なら「割安」
EXPENSIVE_SCORE_THRESHOLD = 33.3  # これ以下なら「割高」
MIN_METRICS_REQUIRED = 2       # 有効指標がこの数未満なら「判定不能」扱い

COLOR_CHEAP = "#2E8B4F"      # 割安: 緑
COLOR_EXPENSIVE = "#B23A48"  # 割高: 赤
COLOR_NEUTRAL_VAL = "#8A7A1F"  # 中立: 琥珀寄りのグレー
COLOR_NA = "#999999"         # 判定不能: グレー

# --- 割安度を構成する指標 (方向: "low"=低いほど割安 / "high"=高いほど割安) ---
VALUATION_METRICS = [
    {"key": "per", "label": "PER", "direction": "low", "fmt": "{:.1f}倍"},
    {"key": "peg", "label": "PEG", "direction": "low", "fmt": "{:.2f}"},
    {"key": "pbr", "label": "PBR", "direction": "low", "fmt": "{:.1f}倍"},
    {"key": "ev_ebitda", "label": "EV/EB", "direction": "low", "fmt": "{:.1f}倍"},
    {"key": "div_yield", "label": "配当", "direction": "high", "fmt": "{:.1f}%"},
    {"key": "fcf_yield", "label": "FCF", "direction": "high", "fmt": "{:.1f}%"},
]

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


def fetch_metrics(symbol: str) -> dict:
    """
    銘柄の割安度判定に使う生指標 (PER/PEG/PBR/EV/EBITDA/配当利回り/FCF利回り) を取得する。
    符号がおかしく比較不能な値 (マイナスのPER/PEG/PBR/EV-EBITDA) は None にする。
    取得失敗時はリトライし、最終的に全指標 None の dict を返して処理は継続させる。
    """
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            info = yf.Ticker(symbol).get_info()

            per = info.get("trailingPE") or info.get("forwardPE")
            peg = info.get("pegRatio") or info.get("trailingPegRatio")
            pbr = info.get("priceToBook")
            ev_ebitda = info.get("enterpriseToEbitda")
            div_yield = info.get("dividendYield")  # 既に%表記 (例 2.8 = 2.8%)

            market_cap = info.get("marketCap")
            fcf = info.get("freeCashflow")
            fcf_yield = (fcf / market_cap * 100) if (fcf is not None and market_cap) else None

            def positive_or_none(v):
                return v if (v is not None and v > 0) else None

            return {
                "per": positive_or_none(per),
                "peg": positive_or_none(peg),
                "pbr": positive_or_none(pbr),
                "ev_ebitda": positive_or_none(ev_ebitda),
                "div_yield": div_yield if div_yield is not None else None,
                "fcf_yield": fcf_yield,
            }
        except Exception as e:
            last_err = e
            print(f"[警告] {symbol} の指標取得に失敗 (試行 {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_WAIT_SEC)
    print(f"[警告] [エラー発生源: fetch_metrics({symbol})] "
          f"{MAX_RETRIES}回の試行後も取得できませんでした。最終エラー: {last_err}")
    return {m["key"]: None for m in VALUATION_METRICS}


def add_valuation(df: pd.DataFrame) -> pd.DataFrame:
    """
    各銘柄に複数指標を取得し、カテゴリ内パーセンタイル順位を指標間で
    平均した「総合割安度スコア (0-100、高いほど割安)」を付与する。
    """
    df = df.copy()
    metrics = [fetch_metrics(sym) for sym in df["Symbol"]]
    for m in VALUATION_METRICS:
        df[m["key"]] = [row[m["key"]] for row in metrics]

    # --- 指標ごとにカテゴリ内パーセンタイル順位 (0-1, 高いほど割安) を計算 ---
    cheapness_cols = []
    for m in VALUATION_METRICS:
        col = m["key"]
        pct_rank = df[col].rank(pct=True, ascending=True)  # 生値が大きいほど1.0に近い
        cheapness = pct_rank if m["direction"] == "high" else (1.0 - pct_rank)
        df[f"{col}_cheapness"] = cheapness
        cheapness_cols.append(f"{col}_cheapness")

    df["score"] = df[cheapness_cols].mean(axis=1, skipna=True) * 100
    df["n_metrics"] = df[cheapness_cols].notna().sum(axis=1)

    def classify(row):
        score, n = row["score"], row["n_metrics"]
        if pd.isna(score) or n < MIN_METRICS_REQUIRED:
            return f"総合判定不能 ({int(n)}/{len(VALUATION_METRICS)}指標)", COLOR_NA
        if score >= CHEAP_SCORE_THRESHOLD:
            return f"割安 ({score:.0f}点・{int(n)}/{len(VALUATION_METRICS)}指標)", COLOR_CHEAP
        if score <= EXPENSIVE_SCORE_THRESHOLD:
            return f"割高 ({score:.0f}点・{int(n)}/{len(VALUATION_METRICS)}指標)", COLOR_EXPENSIVE
        return f"中立 ({score:.0f}点・{int(n)}/{len(VALUATION_METRICS)}指標)", COLOR_NEUTRAL_VAL

    classified = df.apply(classify, axis=1)
    df["ValuationText"] = [c[0] for c in classified]
    df["ValuationColor"] = [c[1] for c in classified]

    # 主要指標の内訳テキスト (取得できたものだけ、チャートの補足行に使う)
    def detail_text(row):
        parts = []
        for m in VALUATION_METRICS:
            v = row[m["key"]]
            if pd.notna(v):
                parts.append(f"{m['label']}{m['fmt'].format(v)}")
        return "  ".join(parts) if parts else "指標取得不可"

    df["DetailText"] = df.apply(detail_text, axis=1)

    median_score = df["score"].median() if df["score"].notna().any() else None
    return df, median_score


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


FOOTNOTE_TEXT = (
    "※ 保有比率は各ETFの上位保有銘柄(概ね上位10銘柄)をETF間で均等加重平均して再ランキングしたものです。\n"
    "※ 総合割安度スコアはPER・PEG・PBR・EV/EBITDA・配当利回り・FCF利回りをカテゴリ内でパーセンタイル順位化し"
    "平均したもの(0-100、高いほど割安)。66.7点以上=割安 / 33.3点以下=割高。取得できた指標数が少ないほど参考程度に。\n"
    "※ 本来のフェアバリュー(理論株価)ではなく、あくまで同カテゴリ内の他銘柄との相対比較です。"
    "緑=割安 / 琥珀=中立 / 赤=割高 / 灰=判定不能。"
)


def plot_category_panel(ax, cat: dict):
    """1カテゴリ分(上位TOP_N銘柄の横棒グラフ)を指定のaxに描く。"""
    df = cat["holdings"].iloc[::-1]  # 上位が上に来るよう反転(barhは下から描画されるため)
    y_pos = range(len(df))
    ax.barh(y_pos, df["Weight"] * 100, color=cat["color"], height=0.65)

    for y, (_, row) in zip(y_pos, df.iterrows()):
        ax.text(row["Weight"] * 100 + 0.3, y + 0.20, f"{row['Name']}  ({row['Weight']*100:.1f}%)",
                va="center", fontsize=8.5, color="#333333")
        ax.text(row["Weight"] * 100 + 0.3, y - 0.05, row["ValuationText"],
                va="center", fontsize=7.8, color=row["ValuationColor"], weight="bold")
        ax.text(row["Weight"] * 100 + 0.3, y - 0.30, row["DetailText"],
                va="center", fontsize=6.5, color="#999999")

    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(df["Symbol"], fontsize=10, weight="bold")
    ax.set_xlim(0, max(df["Weight"] * 100) * 3.1)
    median_score = cat.get("median_score")
    median_text = f"総合割安度スコア中央値: {median_score:.0f}点" if median_score is not None else "総合割安度スコア中央値: 算出不可"
    ax.set_title(f"{cat['label']}\n({'+'.join(cat['tickers'])})  {median_text}",
                 fontsize=12.5, color=cat["color"], loc="left")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(left=False)
    ax.grid(True, axis="x", color="#EEEEEE", lw=0.6)


def plot_categories(category_data: list, stamp: str) -> str:
    """PC/exe向け: カテゴリを横並びにしたレイアウト。"""
    fig, axes = plt.subplots(1, len(category_data), figsize=(6.2 * len(category_data), 7.5))
    if len(category_data) == 1:
        axes = [axes]

    for ax, cat in zip(axes, category_data):
        plot_category_panel(ax, cat)

    fig.suptitle(f"米国株 カテゴリ別 代表銘柄 (上位{TOP_N}銘柄) — データ基準日: {stamp}", fontsize=15, y=1.03)
    fig.text(0.0, -0.06, FOOTNOTE_TEXT, fontsize=8, color="#777777")

    save_path = os.path.join(OUTPUT_DIR, f"06_sector_rotation_top_holdings_{stamp}.png")
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return save_path


def plot_categories_mobile(category_data: list, stamp: str) -> str:
    """Androidアプリ向け: カテゴリを縦に積んだレイアウト (スマホの画面比率に近づける)。"""
    fig, axes = plt.subplots(
        len(category_data), 1, figsize=(6.5, 5.8 * len(category_data)),
        constrained_layout=True,
    )
    if len(category_data) == 1:
        axes = [axes]

    for ax, cat in zip(axes, category_data):
        plot_category_panel(ax, cat)

    fig.suptitle(f"米国株 カテゴリ別 代表銘柄 (上位{TOP_N}銘柄)\nデータ基準日: {stamp}", fontsize=13)
    fig.get_layout_engine().set(hspace=0.08)

    # 脚注はconstrained_layoutのレイアウト計算対象外に置きたいので、
    # 先にsavefigした後、下に帯を追加する形でPillowで合成する。
    save_path = os.path.join(OUTPUT_DIR, f"06_sector_rotation_top_holdings_mobile_{stamp}.png")
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    _append_footnote(save_path)
    return save_path


def _append_footnote(image_path: str):
    """保存済みPNGの下に、脚注テキストを描いた帯を合成して追加する。"""
    from PIL import Image, ImageDraw, ImageFont

    base = Image.open(image_path).convert("RGB")
    font_path = next(
        (f.fname for f in fm.fontManager.ttflist if f.name == plt.rcParams["font.family"][0]),
        None,
    )
    font = ImageFont.truetype(font_path, 15) if font_path else ImageFont.load_default()
    lines = FOOTNOTE_TEXT.split("\n")
    line_h = 22
    footnote_h = line_h * len(lines) + 20

    canvas = Image.new("RGB", (base.width, base.height + footnote_h), "white")
    canvas.paste(base, (0, 0))
    draw = ImageDraw.Draw(canvas)
    y = base.height + 12
    for line in lines:
        draw.text((16, y), line, fill=(119, 119, 119), font=font)
        y += line_h
    canvas.save(image_path)


def main():
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")

        category_data = []
        for cat in CATEGORIES:
            print(f"[情報] {cat['label']} ({'+'.join(cat['tickers'])}) の保有銘柄を取得中...")
            holdings = aggregate_category(cat["tickers"])
            print(f"  割安度指標(PER/PEG/PBR/EV-EBITDA/配当利回り/FCF利回り)を取得中... "
                  f"({', '.join(holdings['Symbol'])})")
            holdings, median_score = add_valuation(holdings)
            category_data.append({**cat, "holdings": holdings, "median_score": median_score})
            top3 = ", ".join(f"{r.Symbol}({r.Weight*100:.1f}%, {r.ValuationText})"
                             for r in holdings.head(3).itertuples())
            print(f"  上位3銘柄: {top3}")

        save_path = plot_categories(category_data, stamp)
        save_path_mobile = plot_categories_mobile(category_data, stamp)
        print(f"\n[完了] 代表銘柄画像を保存しました: {save_path}")
        print(f"[完了] モバイル版代表銘柄画像を保存しました: {save_path_mobile}")

    except Exception as e:
        print(f"\n[エラー] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
