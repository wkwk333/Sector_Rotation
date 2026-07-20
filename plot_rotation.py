# -*- coding: utf-8 -*-
"""
plot_rotation.py
=================
sector_rotation_monitor.py が output/ に出力した各ペアの最新日次CSVを読み込み、
ペアごとにローテーション状況を1枚のグラフ(2段組)として可視化します。
CONFIG["pairs"] に登録されている全ペア分、グラフを個別に生成します。

上段: rotation_ratio と短期/長期移動平均。背景をシグナルで色分けし、
      どちらのバスケットが優位だったかを一目で追えるようにしています。
下段: 流入側・流出側バスケット指数(初日=1.0)そのものの推移。
      レシオだけでは分からない「どちらが上げてどちらが下げたか」を補足します。

実行方法:
    python plot_rotation.py

出力:
    ./output/sector_rotation_chart_<ペア名>_YYYYMMDD.png (ペアごと)
"""

import console_utf8  # noqa: F401  (文字化け対策。最初にimportする)

import glob
import os
import sys

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

from sector_rotation_monitor import CONFIG

OUTPUT_DIR = CONFIG["output_dir"]

# --- 日本語フォントの自動選択 (文字化け対策) ---
_JP_FONT_CANDIDATES = ["Yu Gothic", "Meiryo", "MS Gothic", "Noto Sans CJK JP"]
_available = {f.name for f in fm.fontManager.ttflist}
for _name in _JP_FONT_CANDIDATES:
    if _name in _available:
        plt.rcParams["font.family"] = _name
        break
plt.rcParams["axes.unicode_minus"] = False

# --- 配色 (色覚多様性を考慮した識別しやすい配色) ---
COLOR_RATIO = "#2E5EAA"      # rotation_ratio: 濃い青
COLOR_MA_SHORT = "#E27A3F"   # 短期MA: オレンジ
COLOR_MA_LONG = "#6B6B6B"    # 長期MA: グレー(破線)
COLOR_INFLOW = "#2E5EAA"     # 流入側バスケット: 青
COLOR_OUTFLOW = "#B23A48"    # 流出側バスケット: 赤茶
BG_INFLOW = "#2E5EAA"        # 流入側優位の背景シェード
BG_OUTFLOW = "#B23A48"       # 流出側優位の背景シェード


def find_latest_daily_csv(out_dir: str, pair_name: str) -> str:
    pattern = os.path.join(out_dir, f"sector_rotation_{pair_name}_[0-9]*.csv")
    candidates = sorted(glob.glob(pattern))
    if not candidates:
        raise FileNotFoundError(
            f"[エラー発生源: find_latest_daily_csv] {pattern} に一致するCSVが"
            f" 見つかりません。先に sector_rotation_monitor.py を実行してください。"
        )
    return candidates[-1]


def load_daily(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path, index_col="Date", parse_dates=True, encoding="utf-8-sig")
    return df


def shade_signal_regions(ax, df: pd.DataFrame):
    """signal列(1=流入優位, -1=流出優位)に応じて背景を薄く塗る。"""
    sig = df["signal"]
    valid_idx = sig.dropna().index
    if valid_idx.empty:
        return

    start = None
    prev_val = None
    dates = df.index

    for i, date in enumerate(dates):
        val = sig.loc[date]
        if pd.isna(val):
            if start is not None:
                ax.axvspan(start, dates[i - 1], color=BG_INFLOW if prev_val == 1 else BG_OUTFLOW, alpha=0.06, lw=0)
                start = None
            prev_val = None
            continue
        if start is None:
            start = date
            prev_val = val
        elif val != prev_val:
            ax.axvspan(start, dates[i - 1], color=BG_INFLOW if prev_val == 1 else BG_OUTFLOW, alpha=0.06, lw=0)
            start = date
            prev_val = val

    if start is not None:
        ax.axvspan(start, dates[-1], color=BG_INFLOW if prev_val == 1 else BG_OUTFLOW, alpha=0.06, lw=0)


def plot(df: pd.DataFrame, pair: dict, inflow_label: str, outflow_label: str, save_path: str):
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(13, 8.5), sharex=True,
        gridspec_kw={"height_ratios": [2, 1.2], "hspace": 0.12},
    )

    # --- 上段: レシオ + 移動平均 + シグナル背景 ---
    shade_signal_regions(ax1, df)
    ax1.plot(df.index, df["rotation_ratio"], color=COLOR_RATIO, lw=1.6, label="rotation_ratio (流入/流出)")
    s, l = pair["ma_short"], pair["ma_long"]
    ma_short_col, ma_long_col = f"ratio_ma{s}", f"ratio_ma{l}"
    ax1.plot(df.index, df[ma_short_col], color=COLOR_MA_SHORT, lw=1.3, label=f"{ma_short_col} (短期)")
    ax1.plot(df.index, df[ma_long_col], color=COLOR_MA_LONG, lw=1.3, ls="--", label=f"{ma_long_col} (長期)")

    # シグナル転換日にマーカー
    changes = df.index[df["signal_change"] == True]
    if len(changes):
        ax1.scatter(changes, df.loc[changes, "rotation_ratio"], color="#222222", s=22, zorder=5,
                    label="シグナル転換日")

    ax1.set_title(f"{pair['label']}: {inflow_label} vs {outflow_label} (直近2年)", fontsize=14, pad=12)
    ax1.set_ylabel("rotation_ratio")
    leg1 = ax1.legend(loc="upper left", frameon=True, fontsize=9)
    leg1.get_frame().set_facecolor("white")
    leg1.get_frame().set_edgecolor("#DDDDDD")
    leg1.get_frame().set_alpha(0.9)
    ax1.grid(True, color="#DDDDDD", lw=0.6)
    ax1.spines[["top", "right"]].set_visible(False)

    # --- 下段: バスケット指数そのもの ---
    ax2.plot(df.index, df["inflow_basket"], color=COLOR_INFLOW, lw=1.4, label=f"流入側 ({inflow_label})")
    ax2.plot(df.index, df["outflow_basket"], color=COLOR_OUTFLOW, lw=1.4, label=f"流出側 ({outflow_label})")
    ax2.axhline(1.0, color="#AAAAAA", lw=0.8, ls=":")
    ax2.set_ylabel("バスケット指数 (初日=1.0)")
    ax2.set_xlabel("日付")
    ax2.legend(loc="upper left", frameon=False, fontsize=9)
    ax2.grid(True, color="#DDDDDD", lw=0.6)
    ax2.spines[["top", "right"]].set_visible(False)

    fig.autofmt_xdate()
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_pair(pair: dict, order: int):
    csv_path = find_latest_daily_csv(OUTPUT_DIR, pair["name"])
    df = load_daily(csv_path)

    inflow_label = "+".join(pair["inflow_tickers"])
    outflow_label = "+".join(pair["outflow_tickers"])

    stamp = os.path.basename(csv_path).replace(f"sector_rotation_{pair['name']}_", "").replace(".csv", "")
    save_path = os.path.join(OUTPUT_DIR, f"{order:02d}_sector_rotation_chart_{pair['name']}_{stamp}.png")

    plot(df, pair, inflow_label, outflow_label, save_path)
    print(f"[完了] [{pair['name']}] グラフを保存しました: {save_path}")


def main():
    had_error = False
    for i, pair in enumerate(CONFIG["pairs"], start=1):
        try:
            plot_pair(pair, i)
        except Exception as e:
            had_error = True
            print(f"[エラー] [{pair['name']}] {e}")
    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
