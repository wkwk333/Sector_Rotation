# -*- coding: utf-8 -*-
"""
generate_dashboard.py
======================
sector_rotation_monitor.py が output/ に出力した各指標の最新日次CSVを読み込み、
資金ローテーション状況を1枚のダッシュボード画像にまとめます。

構成:
    上段: 総合判定 (複数指標の同時悪化度合いから3段階で判定)
    中段以下: 6指標(ペア5本 + VIX)をそれぞれ小パネルで表示。
              直近6か月の推移・移動平均・現在の状態ラベルを表示。

このダッシュボードの読み方は dashboard_guide.txt を参照してください。

実行方法(先に sector_rotation_monitor.py を実行しておくこと):
    python generate_dashboard.py

出力:
    ./output/sector_rotation_dashboard_YYYYMMDD.png
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
LOOKBACK_DAYS = 130  # 直近およそ6か月分の営業日
CONFIRM_WINDOW_DAYS = 5  # シグナル転換からこの営業日数以内は「転換直後・要確認」表示

# --- 日本語フォントの自動選択 (文字化け対策) ---
_JP_FONT_CANDIDATES = ["Yu Gothic", "Meiryo", "MS Gothic", "Noto Sans CJK JP"]
_available = {f.name for f in fm.fontManager.ttflist}
for _name in _JP_FONT_CANDIDATES:
    if _name in _available:
        plt.rcParams["font.family"] = _name
        break
plt.rcParams["axes.unicode_minus"] = False

# --- 配色 ---
COLOR_INFLOW = "#2E5EAA"     # 流入側優位: 青
COLOR_OUTFLOW = "#B23A48"    # 流出側優位: 赤茶
COLOR_NEUTRAL = "#888888"    # レシオ本体の細線

STATUS_COLOR = {
    "good": "#2E8B4F",       # 平常/健全: 緑
    "warning": "#C9902A",    # 警戒: 琥珀
    "critical": "#B23A48",   # パニック的リスクオフ: 赤
}
ZONE_STATUS = {"平常": "good", "警戒": "warning", "パニック的リスクオフ": "critical"}


def find_latest_csv(pattern: str) -> str:
    candidates = sorted(glob.glob(pattern))
    if not candidates:
        raise FileNotFoundError(
            f"[エラー発生源: find_latest_csv] {pattern} に一致するCSVが見つかりません。"
            " 先に sector_rotation_monitor.py を実行してください。"
        )
    return candidates[-1]


def load_pair_df(pair_name: str) -> pd.DataFrame:
    path = find_latest_csv(os.path.join(OUTPUT_DIR, f"sector_rotation_{pair_name}_[0-9]*.csv"))
    return pd.read_csv(path, index_col="Date", parse_dates=True, encoding="utf-8-sig")


def load_level_df(indicator_name: str) -> pd.DataFrame:
    path = find_latest_csv(os.path.join(OUTPUT_DIR, f"sector_rotation_level_{indicator_name}_[0-9]*.csv"))
    return pd.read_csv(path, index_col="Date", parse_dates=True, encoding="utf-8-sig")


def days_since_last_change(df: pd.DataFrame) -> int:
    """
    直近のシグナル転換日から、データ末尾まで何営業日経過しているかを返す。
    転換履歴が無ければ None。0 = 最新日そのものが転換日。
    """
    valid = df.dropna(subset=["signal"])
    changes = valid.index[valid["signal_change"] == True]
    if len(changes) == 0:
        return None
    last_change_date = changes[-1]
    pos_change = valid.index.get_loc(last_change_date)
    pos_last = len(valid) - 1
    return pos_last - pos_change


def draw_spread_gauge(ax, spread: float, scale: float, color: str):
    """
    パネル右上に「短期MA - 長期MA」のスプレッドを表す小さな水平ゲージを描く。
    scale はゲージの表示レンジ(直近ルックバック期間内のスプレッド絶対値の最大)。
    """
    if scale <= 0:
        return
    gauge = ax.inset_axes([0.60, 0.90, 0.38, 0.07])
    gauge.set_xlim(-1.0, 1.0)
    gauge.set_ylim(0, 1)
    gauge.axis("off")
    gauge.axvspan(-1.0, 1.0, color="#EEEEEE", lw=0)
    norm = max(-1.0, min(1.0, spread / scale))
    if norm >= 0:
        gauge.barh(0.5, norm, height=0.9, left=0, color=color, align="center")
    else:
        gauge.barh(0.5, -norm, height=0.9, left=norm, color=color, align="center")
    gauge.axvline(0, color="#888888", lw=0.8)


def signal_label_text(pair: dict, signal_value) -> str:
    labels = pair.get("signal_labels")
    if signal_value == 1:
        return labels[1] if labels else "流入側優位"
    if signal_value == -1:
        return labels[-1] if labels else "流出側優位"
    return "判定不能"


def compute_regime(pair_data: dict, vix_status: str):
    """
    複数指標の同時悪化度合いから、総合的な市況を3段階で判定する。

    見ている「悪化(ストレス)シグナル」:
      - defensive_tech: ディフェンシブが優位 (資金がテックから逃避)
                         ※ このペアはレシオ=テック÷ディフェンシブなので、
                            ディフェンシブ優位は signal == -1 で判定する
      - credit_risk    : IEF(米国債)優位 = 質への逃避 (信用リスク回避)
      - breadth_equalweight: SPY(時価総額加重)優位 = 大型集中・厚みの劣化
      - vix            : 警戒 or パニック的リスクオフ

    3個以上該当 → 広範なリスクオフ / 2個 → 警戒領域 / 0〜1個 → 限定的な動き
    """
    flags = {
        "ディフェンシブ優位": pair_data["defensive_tech"]["signal"] == -1,
        "質への逃避 (credit)": pair_data["credit_risk"]["signal"] == -1,
        "大型集中 (breadth)": pair_data["breadth_equalweight"]["signal"] == -1,
        "VIX警戒以上": vix_status != "平常",
    }
    stress_count = sum(flags.values())

    if stress_count >= 3:
        regime = "広範なリスクオフ (複数指標が同時に悪化)"
        status = "critical"
    elif stress_count == 2:
        regime = "警戒領域 (一部指標に悪化の兆し)"
        status = "warning"
    else:
        regime = "限定的な動き (広範なリスクオフの兆候は薄い、セクター内ローテーションが主体)"
        status = "good"

    return regime, status, stress_count, flags


def plot_pair_panel(ax, pair: dict, df_full: pd.DataFrame) -> dict:
    df = df_full.tail(LOOKBACK_DAYS)
    s, l = pair["ma_short"], pair["ma_long"]
    last = df.dropna(subset=["rotation_ratio"]).iloc[-1]
    signal = last["signal"]
    line_color = COLOR_INFLOW if signal == 1 else (COLOR_OUTFLOW if signal == -1 else COLOR_NEUTRAL)

    ax.plot(df.index, df["rotation_ratio"], color=COLOR_NEUTRAL, lw=0.8, alpha=0.55)
    ax.plot(df.index, df[f"ratio_ma{s}"], color=line_color, lw=2.0)

    # --- スプレッド (短期MA - 長期MA) ---
    spread_series = (df[f"ratio_ma{s}"] - df[f"ratio_ma{l}"]).dropna()
    spread = spread_series.iloc[-1]
    spread_pct = spread / last[f"ratio_ma{l}"] * 100 if last[f"ratio_ma{l}"] else float("nan")
    scale = spread_series.abs().max()
    draw_spread_gauge(ax, spread, scale, line_color)

    # --- 転換直後・要確認バッジ (シグナル転換からCONFIRM_WINDOW_DAYS営業日以内) ---
    days_since = days_since_last_change(df_full)
    flagged = days_since is not None and days_since <= CONFIRM_WINDOW_DAYS
    if flagged:
        ax.text(0.985, 0.80, f"転換直後 ({days_since}営業日前)\n要確認",
                transform=ax.transAxes, fontsize=7, color="white", ha="right", va="top",
                linespacing=1.4,
                bbox=dict(boxstyle="round,pad=0.35", facecolor=STATUS_COLOR["warning"], edgecolor="none", alpha=0.92))

    status_text = signal_label_text(pair, signal)
    ax.set_title(
        f"{pair['label']}\n{status_text}  (ratio={last['rotation_ratio']:.3f})\n"
        f"スプレッド(短期-長期): {spread:+.4f} ({spread_pct:+.1f}%)",
        fontsize=9.5, color=line_color, loc="left")
    ax.tick_params(axis="both", labelsize=7)
    ax.grid(True, color="#E5E5E5", lw=0.5)
    ax.spines[["top", "right"]].set_visible(False)
    for label in ax.get_xticklabels():
        label.set_rotation(30)
        label.set_ha("right")

    return {"label": pair["label"], "flagged": flagged, "days_since": days_since}


def plot_vix_panel(ax, indicator: dict, df: pd.DataFrame):
    df = df.tail(LOOKBACK_DAYS)
    ma = indicator["ma_short"]
    last = df.dropna(subset=["close"]).iloc[-1]
    status = ZONE_STATUS.get(last["zone"], "good")
    color = STATUS_COLOR[status]

    ymax = max(df["close"].max(), 32) * 1.05
    ax.axhspan(0, 20, color=STATUS_COLOR["good"], alpha=0.07, lw=0)
    ax.axhspan(20, 30, color=STATUS_COLOR["warning"], alpha=0.09, lw=0)
    ax.axhspan(30, ymax, color=STATUS_COLOR["critical"], alpha=0.09, lw=0)
    ax.set_ylim(0, ymax)

    ax.plot(df.index, df["close"], color=COLOR_NEUTRAL, lw=0.8, alpha=0.55)
    ax.plot(df.index, df[f"close_ma{ma}"], color=color, lw=2.0)

    ax.set_title(f"{indicator['label']}\n{last['zone']}  (VIX={last['close']:.1f})",
                 fontsize=9.5, color=color, loc="left")
    ax.tick_params(axis="both", labelsize=7)
    ax.grid(True, color="#E5E5E5", lw=0.5)
    ax.spines[["top", "right"]].set_visible(False)
    for label in ax.get_xticklabels():
        label.set_rotation(30)
        label.set_ha("right")


def main():
    try:
        pairs = CONFIG["pairs"]
        vix_indicator = next(i for i in CONFIG["level_indicators"] if i["name"] == "vix")

        pair_dfs = {p["name"]: load_pair_df(p["name"]) for p in pairs}
        vix_df = load_level_df("vix")

        pair_last = {name: df.dropna(subset=["rotation_ratio"]).iloc[-1] for name, df in pair_dfs.items()}
        vix_last_zone = vix_df.dropna(subset=["close"]).iloc[-1]["zone"]

        regime, regime_status, stress_count, flags = compute_regime(pair_last, vix_last_zone)
        regime_color = STATUS_COLOR[regime_status]

        n_panels = len(pairs) + 1  # + VIX
        ncols = 3
        nrows = -(-n_panels // ncols)  # ceil

        # 各ペアの「転換直後・要確認」判定はパネル描画時に確定するが、
        # バナーの高さを事前に決めるため、ここで先に計算しておく。
        pair_flags = {}
        for p in pairs:
            days_since = days_since_last_change(pair_dfs[p["name"]])
            pair_flags[p["name"]] = {
                "label": p["label"],
                "flagged": days_since is not None and days_since <= CONFIRM_WINDOW_DAYS,
                "days_since": days_since,
            }
        flagged_pairs = [v for v in pair_flags.values() if v["flagged"]]

        banner_height = 1.3 if flagged_pairs else 1.1
        fig = plt.figure(figsize=(15, 4.2 * nrows + (2.2 if flagged_pairs else 2.0)))
        gs = fig.add_gridspec(nrows + 1, ncols, height_ratios=[banner_height] + [1] * nrows,
                              hspace=0.75, wspace=0.35)

        # --- 総合判定バナー (縦積みレイアウトで文字被りを回避) ---
        ax_banner = fig.add_subplot(gs[0, :])
        ax_banner.axis("off")
        latest_date = max(df.index.max() for df in pair_dfs.values()).strftime("%Y-%m-%d")
        flag_text = " / ".join(f"{k}: {'○' if v else '-'}" for k, v in flags.items())

        ax_banner.text(0.0, 0.93, "米国株 資金ローテーション ダッシュボード", fontsize=17, weight="bold",
                       transform=ax_banner.transAxes, va="top")
        ax_banner.text(0.0, 0.68, f"総合判定: {regime}", fontsize=13, weight="bold", color=regime_color,
                       transform=ax_banner.transAxes, va="top")
        ax_banner.text(0.0, 0.48, f"データ基準日: {latest_date}    |    悪化シグナル {stress_count}/4 個該当  ({flag_text})",
                       fontsize=9.5, color="#555555", transform=ax_banner.transAxes, va="top")
        if flagged_pairs:
            flagged_text = " / ".join(f"{v['label']} ({v['days_since']}営業日前)" for v in flagged_pairs)
            ax_banner.text(0.0, 0.28, f"転換直後・要確認 (直近{CONFIRM_WINDOW_DAYS}営業日以内にシグナル転換): {flagged_text}",
                           fontsize=9.5, weight="bold", color=STATUS_COLOR["warning"],
                           transform=ax_banner.transAxes, va="top")
        ax_banner.axhline(0.03, color="#DDDDDD", lw=1)

        # --- 各指標パネル ---
        panel_specs = [("pair", p) for p in pairs] + [("vix", vix_indicator)]
        for i, (kind, spec) in enumerate(panel_specs):
            row, col = divmod(i, ncols)
            ax = fig.add_subplot(gs[row + 1, col])
            if kind == "pair":
                plot_pair_panel(ax, spec, pair_dfs[spec["name"]])
            else:
                plot_vix_panel(ax, spec, vix_df)

        stamp = os.path.basename(find_latest_csv(
            os.path.join(OUTPUT_DIR, "sector_rotation_summary_[0-9]*.csv")
        )).replace("sector_rotation_summary_", "").replace(".csv", "")
        save_path = os.path.join(OUTPUT_DIR, f"sector_rotation_dashboard_{stamp}.png")
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.close(fig)

        print(f"[完了] ダッシュボードを保存しました: {save_path}")
        print(f"[情報] 総合判定: {regime}")

    except Exception as e:
        print(f"[エラー] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
