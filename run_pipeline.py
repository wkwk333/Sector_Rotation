# -*- coding: utf-8 -*-
"""
run_pipeline.py
================
sector_rotation_monitor.py → plot_rotation.py → generate_dashboard.py を
1つのプロセス内で順番に実行する統合エントリーポイント。
PyInstaller で1個の .exe (SectorRotationDashboard.exe) にまとめる際の
起点としても使う (build_exe.ps1 参照)。

実行方法:
    python run_pipeline.py
    (exe化した場合は SectorRotationDashboard.exe をダブルクリック)

出力:
    ./output/ 以下に日次CSV・ペア別グラフ・ダッシュボード画像一式
"""
import console_utf8  # noqa: F401  (文字化け対策。最初にimportする)

import sys
import traceback

import sector_rotation_monitor
import plot_rotation
import generate_dashboard


def main():
    steps = [
        ("データ取得・レシオ計算", sector_rotation_monitor.main),
        ("ペア別グラフ作成", plot_rotation.main),
        ("ダッシュボード作成", generate_dashboard.main),
    ]
    for i, (label, func) in enumerate(steps, start=1):
        print("\n" + "#" * 60)
        print(f"# ステップ {i}/{len(steps)}: {label}")
        print("#" * 60)
        try:
            func()
        except SystemExit as e:
            if e.code not in (0, None):
                print(f"\n[中断] ステップ「{label}」でエラーが発生したため処理を終了します。")
                raise

    print("\n" + "=" * 60)
    print("すべての処理が完了しました。output フォルダを確認してください。")
    print("=" * 60)


if __name__ == "__main__":
    exit_code = 0
    try:
        main()
    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else 1
    except Exception:
        traceback.print_exc()
        exit_code = 1
    finally:
        # exe としてダブルクリック実行された場合は、結果を確認できるよう
        # ウィンドウが即座に閉じないようキー入力を待つ。
        if getattr(sys, "frozen", False):
            input("\n何かキーを押すと終了します...")
    sys.exit(exit_code)
