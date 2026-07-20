# -*- coding: utf-8 -*-
"""
sector_rotation_monitor.py
==========================
米国株 資金ローテーション監視スクリプト (複数ペア + レベル指標 対応)

2種類の指標を扱います。

1. ペア指標 (CONFIG["pairs"]) : 流入側バスケット ÷ 流出側バスケット のレシオ。
   移動平均クロスで「どちらが優位か」のシグナルを判定します。
     ① value_tech          : バリュー/景気循環 (XLF+XLI+XLE) vs テック (SMH+IGV)
     ② defensive_tech      : ディフェンシブ (XLP+XLU+XLV) vs テック (XLK+SMH+IGV)
     ③ breadth_smallcap    : 小型 (IWM) vs 大型 (SPY) … 景気敏感度・裾野の広さ
     ④ breadth_equalweight : 等ウェイトS&P (RSP) vs 時価総額加重S&P (SPY) … 大型集中度
     ⑤ credit_risk         : ハイイールド社債 (HYG) vs 米国債7-10y (IEF) … 信用リスク選好度

2. レベル指標 (CONFIG["level_indicators"]) : 単一銘柄の水準そのものをゾーン判定。
     ⑥ vix : VIX指数 (^VIX) … 平常/警戒/パニック的リスクオフ

必要ライブラリ:
    pip install yfinance pandas numpy

実行方法:
    python sector_rotation_monitor.py

出力:
    ./output/sector_rotation_<ペア名>_YYYYMMDD.csv        (ペアごとの日次データ)
    ./output/sector_rotation_level_<指標名>_YYYYMMDD.csv  (レベル指標ごとの日次データ)
    ./output/sector_rotation_summary_YYYYMMDD.csv          (ペアまとめのサマリー)
    ./output/sector_rotation_summary_levels_YYYYMMDD.csv   (レベル指標まとめのサマリー)

このあと generate_dashboard.py を実行すると、上記すべてを1枚にまとめた
ダッシュボード画像を作成できます (読み方は dashboard_guide.txt 参照)。
"""

import console_utf8  # noqa: F401  (文字化け対策。最初にimportする)

import os
import sys
import time
import traceback
from datetime import datetime

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    print("[致命的エラー] yfinance がインストールされていません。")
    print("  対処: pip install yfinance を実行してください。")
    sys.exit(1)

# ============================================================
# 設定セクション (ここだけ書き換えれば構成変更できます)
# ============================================================
CONFIG = {
    "pairs": [
        {
            "name": "value_tech",
            "label": "①バリュー/景気循環 vs テック",
            "inflow_tickers": ["XLF", "XLI", "XLE"],
            "outflow_tickers": ["SMH", "IGV"],
            # ダマシ増加を許容し、②と同じ短期MAで判定を速くする
            "ma_short": 10,
            "ma_long": 30,
            "signal_labels": {1: "バリュー優位", -1: "テック優位"},
        },
        {
            "name": "defensive_tech",
            "label": "②ディフェンシブ vs テック",
            "inflow_tickers": ["XLP", "XLU", "XLV"],
            "outflow_tickers": ["XLK", "SMH", "IGV"],
            # 直近の動きは数週間と短いため短期MAを採用
            "ma_short": 10,
            "ma_long": 30,
            "signal_labels": {1: "ディフェンシブ優位", -1: "テック優位"},
        },
        {
            "name": "breadth_smallcap",
            "label": "③小型 vs 大型 (景気敏感度)",
            "inflow_tickers": ["IWM"],
            "outflow_tickers": ["SPY"],
            "ma_short": 20,
            "ma_long": 60,
            "signal_labels": {1: "小型優位 (裾野拡大)", -1: "大型優位 (選別的)"},
        },
        {
            "name": "breadth_equalweight",
            "label": "④市場の厚み (等ウェイト vs 時価総額加重)",
            "inflow_tickers": ["RSP"],
            "outflow_tickers": ["SPY"],
            "ma_short": 20,
            "ma_long": 60,
            "signal_labels": {1: "厚み良好 (等ウェイト優位)", -1: "大型集中 (SPY優位)"},
        },
        {
            "name": "credit_risk",
            "label": "⑤信用リスク選好度 (ハイイールド社債 vs 米国債)",
            "inflow_tickers": ["HYG"],
            "outflow_tickers": ["IEF"],
            "ma_short": 20,
            "ma_long": 60,
            "signal_labels": {1: "信用リスクオン (HYG優位)", -1: "質への逃避 (IEF優位)"},
        },
    ],
    "level_indicators": [
        {
            "name": "vix",
            "label": "⑥VIX指数 (恐怖指数)",
            "ticker": "^VIX",
            "ma_short": 10,
            # (下限, 上限, ラベル)
            "zones": [
                (0, 20, "平常"),
                (20, 30, "警戒"),
                (30, float("inf"), "パニック的リスクオフ"),
            ],
        },
    ],
    # 取得期間 (例: "1y", "2y", "5y", "max")
    "period": "2y",
    # データ取得リトライ回数と待機秒数
    "max_retries": 3,
    "retry_wait_sec": 5,
    # 出力ディレクトリ
    "output_dir": "output",
}
# ============================================================


def download_prices(tickers: list, period: str) -> pd.DataFrame:
    """
    yfinance で調整後終値を取得し、列=ティッカーの DataFrame を返す。

    エラー対処:
    - MultiIndex 列 (複数ティッカー時の (Price, Ticker) 構造) を平坦化
    - 単一ティッカー時の Series/DataFrame の差異を吸収
    - 取得失敗ティッカーを明示的に報告
    - 空データ・全NaN列を検出して除外
    """
    last_err = None
    for attempt in range(1, CONFIG["max_retries"] + 1):
        try:
            raw = yf.download(
                tickers,
                period=period,
                auto_adjust=True,   # 調整後価格 (配当・分割調整済み)
                progress=False,
                group_by="column",
            )
            if raw is None or raw.empty:
                raise ValueError("yf.download が空のデータを返しました")
            break
        except Exception as e:
            last_err = e
            print(f"[警告] データ取得失敗 (試行 {attempt}/{CONFIG['max_retries']}): {e}")
            if attempt < CONFIG["max_retries"]:
                time.sleep(CONFIG["retry_wait_sec"])
    else:
        raise RuntimeError(
            f"[エラー発生源: download_prices] {CONFIG['max_retries']}回の試行後も"
            f"データ取得に失敗しました。最終エラー: {last_err}\n"
            "  対処: ネットワーク接続、ティッカー名、yfinanceのバージョンを確認してください。"
        )

    # --- Close 列の抽出 (MultiIndex / 単一Index 両対応) ---
    if isinstance(raw.columns, pd.MultiIndex):
        # 複数ティッカー時: 第1階層に 'Close' がある構造
        if "Close" in raw.columns.get_level_values(0):
            close = raw["Close"].copy()
        else:
            raise KeyError(
                "[エラー発生源: download_prices] MultiIndex 列に 'Close' が見つかりません。"
                f" 実際の列: {list(raw.columns.get_level_values(0).unique())}"
            )
    else:
        # 単一ティッカー時: フラットな列構造
        if "Close" in raw.columns:
            close = raw[["Close"]].copy()
            close.columns = [tickers[0]] if len(tickers) == 1 else close.columns
        else:
            raise KeyError(
                "[エラー発生源: download_prices] 列に 'Close' が見つかりません。"
                f" 実際の列: {list(raw.columns)}"
            )

    # Series になってしまった場合の保険 (DataFrame に統一)
    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0])

    # --- 欠損ティッカーの検出と報告 ---
    missing = [t for t in tickers if t not in close.columns]
    if missing:
        print(f"[警告] 次のティッカーは取得できませんでした: {missing}")

    all_nan = [c for c in close.columns if close[c].isna().all()]
    if all_nan:
        print(f"[警告] 全期間 NaN のため除外: {all_nan}")
        close = close.drop(columns=all_nan)

    if close.empty or close.shape[1] == 0:
        raise ValueError(
            "[エラー発生源: download_prices] 有効な価格データが1本もありません。"
            " ティッカー名を確認してください。"
        )

    # 前方補填 (休場日ズレ対策)。先頭のNaNは残す。
    close = close.ffill()
    return close


def build_basket(close: pd.DataFrame, tickers: list, label: str) -> pd.Series:
    """
    各ティッカーを初日=1.0に正規化し、等ウェイト平均でバスケット指数を作る。
    構成銘柄が1本のみの場合は、その銘柄を正規化しただけの系列になる。

    エラー対処:
    - バスケット構成銘柄が1本も無い場合は明示的に停止
    - 一部欠損時は残った銘柄で計算し警告を出す (アライメントは index 結合で保証)
    """
    available = [t for t in tickers if t in close.columns]
    if not available:
        raise ValueError(
            f"[エラー発生源: build_basket({label})] 構成銘柄 {tickers} が"
            " 1本も取得できていません。"
        )
    if len(available) < len(tickers):
        print(f"[警告] {label} バスケットは {available} のみで計算します"
              f" (欠損: {set(tickers) - set(available)})")

    sub = close[available].dropna(how="all")
    # 各列の最初の有効値で正規化 (列ごとに基準日が微妙に違ってもOK)
    first_valid = sub.apply(lambda s: s.loc[s.first_valid_index()])
    normalized = sub.div(first_valid, axis=1)
    basket = normalized.mean(axis=1, skipna=True)
    basket.name = label
    return basket


def compute_rotation(close: pd.DataFrame, pair: dict) -> pd.DataFrame:
    """
    指定ペアのバスケット・レシオ・移動平均・シグナルをまとめた DataFrame を返す。
    Series 同士の演算は pandas の index 自動アライメントに任せ、
    最後に dropna で共通期間に揃える。
    """
    inflow_tickers = pair["inflow_tickers"]
    outflow_tickers = pair["outflow_tickers"]
    pair_tickers = [t for t in inflow_tickers + outflow_tickers if t in close.columns]

    inflow = build_basket(close, inflow_tickers, "inflow_basket")
    outflow = build_basket(close, outflow_tickers, "outflow_basket")

    df = pd.concat([close[pair_tickers], inflow, outflow], axis=1)

    # レシオ: 上昇 = 流入側バスケットが優位 (このペアの軸でのローテーション進行)
    df["rotation_ratio"] = df["inflow_basket"] / df["outflow_basket"]

    s, l = pair["ma_short"], pair["ma_long"]
    df[f"ratio_ma{s}"] = df["rotation_ratio"].rolling(s, min_periods=s).mean()
    df[f"ratio_ma{l}"] = df["rotation_ratio"].rolling(l, min_periods=l).mean()

    # シグナル: 短期MA > 長期MA なら 1 (流入側優位)、逆なら -1
    cond = df[f"ratio_ma{s}"] > df[f"ratio_ma{l}"]
    df["signal"] = np.where(df[f"ratio_ma{l}"].isna(), np.nan,
                            np.where(cond, 1, -1))

    # シグナル転換日の検出 (前日と符号が変わった日)
    df["signal_change"] = df["signal"].diff().fillna(0) != 0
    df.loc[df["signal"].isna(), "signal_change"] = False

    return df


def classify_zone(value: float, zones: list) -> str:
    """value が属するゾーンのラベルを返す (zones = [(下限, 上限, ラベル), ...])。"""
    for lo, hi, label in zones:
        if lo <= value < hi:
            return label
    return "判定不能"


def compute_level(close: pd.DataFrame, indicator: dict) -> pd.DataFrame:
    """
    単一銘柄の水準を追うレベル指標 (例: VIX) の DataFrame を返す。
    列: close, close_ma{N}, zone
    """
    ticker = indicator["ticker"]
    if ticker not in close.columns:
        raise ValueError(
            f"[エラー発生源: compute_level({indicator['name']})] "
            f"ティッカー {ticker} が取得できていません。"
        )
    s = close[ticker].dropna()
    df = s.to_frame(name="close")

    ma = indicator["ma_short"]
    df[f"close_ma{ma}"] = df["close"].rolling(ma, min_periods=ma).mean()
    df["zone"] = df["close"].apply(lambda v: classify_zone(v, indicator["zones"]))

    return df


def signal_label_text(pair: dict, signal_value) -> str:
    """シグナル値(1/-1/NaN)を、ペア固有の読みやすいラベルに変換する。"""
    labels = pair.get("signal_labels")
    if signal_value == 1:
        return labels[1] if labels else "流入側優位"
    if signal_value == -1:
        return labels[-1] if labels else "流出側優位"
    return "判定不能"


def save_daily(df: pd.DataFrame, filename: str, out_dir: str) -> str:
    path = os.path.join(out_dir, filename)
    df.to_csv(path, encoding="utf-8-sig", index_label="Date")
    return path


def build_summary_row(df: pd.DataFrame, pair: dict) -> dict:
    """ペアごとのサマリー1行分 (dict) を作る。"""
    valid = df.dropna(subset=["rotation_ratio"])
    if valid.empty:
        raise ValueError(
            f"[エラー発生源: build_summary_row({pair['name']})] "
            "有効なレシオが計算できていません。"
        )

    last = valid.iloc[-1]
    changes = df.index[df["signal_change"]]
    last_change = changes[-1].strftime("%Y-%m-%d") if len(changes) else "期間内なし"

    s, l = pair["ma_short"], pair["ma_long"]
    return {
        "ペア": pair["name"],
        "軸": pair["label"],
        "最終データ日": valid.index[-1].strftime("%Y-%m-%d"),
        "rotation_ratio (流入/流出)": round(float(last["rotation_ratio"]), 4),
        "短期MA日数": s,
        "短期MA値": round(float(last[f"ratio_ma{s}"]), 4) if pd.notna(last[f"ratio_ma{s}"]) else "計算前",
        "長期MA日数": l,
        "長期MA値": round(float(last[f"ratio_ma{l}"]), 4) if pd.notna(last[f"ratio_ma{l}"]) else "計算前",
        "現在のシグナル": signal_label_text(pair, last["signal"]),
        "直近のシグナル転換日": last_change,
        "流入側バスケット構成": "+".join(pair["inflow_tickers"]),
        "流出側バスケット構成": "+".join(pair["outflow_tickers"]),
    }


def build_level_summary_row(df: pd.DataFrame, indicator: dict) -> dict:
    """レベル指標ごとのサマリー1行分 (dict) を作る。"""
    valid = df.dropna(subset=["close"])
    if valid.empty:
        raise ValueError(
            f"[エラー発生源: build_level_summary_row({indicator['name']})] "
            "有効な値が計算できていません。"
        )
    last = valid.iloc[-1]
    ma = indicator["ma_short"]
    return {
        "指標": indicator["name"],
        "軸": indicator["label"],
        "最終データ日": valid.index[-1].strftime("%Y-%m-%d"),
        "現在値": round(float(last["close"]), 2),
        "短期MA日数": ma,
        "短期MA値": round(float(last[f"close_ma{ma}"]), 2) if pd.notna(last[f"close_ma{ma}"]) else "計算前",
        "状態": last["zone"],
        "対象銘柄": indicator["ticker"],
    }


def main():
    print("=" * 60)
    print("米国株 資金ローテーション監視スクリプト")
    for pair in CONFIG["pairs"]:
        print(f"  [{pair['name']}] {pair['label']}: "
              f"流入={pair['inflow_tickers']} / 流出={pair['outflow_tickers']} "
              f"/ MA {pair['ma_short']}日・{pair['ma_long']}日")
    for ind in CONFIG["level_indicators"]:
        print(f"  [{ind['name']}] {ind['label']}: {ind['ticker']} / MA {ind['ma_short']}日")
    print(f"  期間: {CONFIG['period']}")
    print("=" * 60)

    try:
        out_dir = CONFIG["output_dir"]
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")

        # 全ペア・全レベル指標分のティッカーをまとめて1回で取得 (API呼び出し削減)
        pair_tickers = {t for pair in CONFIG["pairs"]
                        for t in pair["inflow_tickers"] + pair["outflow_tickers"]}
        level_tickers = {ind["ticker"] for ind in CONFIG["level_indicators"]}
        all_tickers = sorted(pair_tickers | level_tickers)
        close = download_prices(all_tickers, CONFIG["period"])
        print(f"[情報] 価格データ取得完了: {close.shape[0]}営業日 × {close.shape[1]}銘柄")

        # --- ペア指標 ---
        pair_summary_rows = []
        for pair in CONFIG["pairs"]:
            df = compute_rotation(close, pair)
            daily_path = save_daily(df, f"sector_rotation_{pair['name']}_{stamp}.csv", out_dir)
            pair_summary_rows.append(build_summary_row(df, pair))
            print(f"[完了] [{pair['name']}] 日次データ: {daily_path}")

        pair_summary = pd.DataFrame(pair_summary_rows)
        pair_summary_path = os.path.join(out_dir, f"sector_rotation_summary_{stamp}.csv")
        pair_summary.to_csv(pair_summary_path, encoding="utf-8-sig", index=False)
        print(f"[完了] ペアサマリー: {pair_summary_path}\n")
        print(pair_summary.to_string(index=False))

        # --- レベル指標 ---
        level_summary_rows = []
        for ind in CONFIG["level_indicators"]:
            df = compute_level(close, ind)
            daily_path = save_daily(df, f"sector_rotation_level_{ind['name']}_{stamp}.csv", out_dir)
            level_summary_rows.append(build_level_summary_row(df, ind))
            print(f"\n[完了] [{ind['name']}] 日次データ: {daily_path}")

        level_summary = pd.DataFrame(level_summary_rows)
        level_summary_path = os.path.join(out_dir, f"sector_rotation_summary_levels_{stamp}.csv")
        level_summary.to_csv(level_summary_path, encoding="utf-8-sig", index=False)
        print(f"[完了] レベル指標サマリー: {level_summary_path}\n")
        print(level_summary.to_string(index=False))

        print("\n[次のステップ] generate_dashboard.py を実行すると、"
              "全指標を1枚にまとめたダッシュボード画像が作成できます。")

    except Exception as e:
        print("\n[エラー] 処理が中断されました。")
        print(f"  内容: {e}")
        print("  --- 詳細トレースバック ---")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
