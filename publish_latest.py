# -*- coding: utf-8 -*-
"""
publish_latest.py
==================
output/ にある最新のダッシュボード画像・代表銘柄画像を、固定ファイル名で
public/ にコピーし、生成日時とファイル名を記載した latest.json を書き出す。

public/ はGitHub Pagesへそのままアップロードされる想定のディレクトリで、
Androidアプリ(別リポジトリ)はここに書き出された latest.json を起点に
画像を取得する。

実行方法(先に sector_rotation_monitor.py / top_holdings.py を実行しておくこと):
    python publish_latest.py

出力:
    ./public/latest_dashboard.png
    ./public/latest_top_holdings.png
    ./public/latest.json
"""

import console_utf8  # noqa: F401  (文字化け対策。最初にimportする)

import glob
import json
import os
import shutil
import sys
from datetime import datetime, timezone

from sector_rotation_monitor import CONFIG

OUTPUT_DIR = CONFIG["output_dir"]
PUBLISH_DIR = "public"


def find_latest(pattern: str) -> str:
    matches = sorted(glob.glob(pattern))
    if not matches:
        raise FileNotFoundError(
            f"[エラー発生源: find_latest] {pattern} に一致するファイルがありません。"
            " 先に sector_rotation_monitor.py / plot_rotation.py / generate_dashboard.py"
            " / top_holdings.py を実行してください。"
        )
    return matches[-1]


def main():
    try:
        os.makedirs(PUBLISH_DIR, exist_ok=True)

        dashboard_src = find_latest(os.path.join(OUTPUT_DIR, "sector_rotation_dashboard_*.png"))
        top_holdings_src = find_latest(os.path.join(OUTPUT_DIR, "06_sector_rotation_top_holdings_*.png"))

        shutil.copy2(dashboard_src, os.path.join(PUBLISH_DIR, "latest_dashboard.png"))
        shutil.copy2(top_holdings_src, os.path.join(PUBLISH_DIR, "latest_top_holdings.png"))

        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dashboard_url": "latest_dashboard.png",
            "top_holdings_url": "latest_top_holdings.png",
        }
        manifest_path = os.path.join(PUBLISH_DIR, "latest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        print(f"[完了] 公開用ファイルを {PUBLISH_DIR}/ に書き出しました。")
        print(f"  dashboard: {dashboard_src} -> {PUBLISH_DIR}/latest_dashboard.png")
        print(f"  top_holdings: {top_holdings_src} -> {PUBLISH_DIR}/latest_top_holdings.png")
        print(f"  manifest: {manifest}")

    except Exception as e:
        print(f"\n[エラー] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
