"""保存先のパス。

データ・設定・ログはすべて %LOCALAPPDATA%\\ApprovalReport\\ に保存する（exe の配置場所が書込不可でも動くように）。
【仮定】仕様の %LOCALAPPDATA%\\WBSAdvisor\\ は wbs_advisor と同じフォルダになるため、ApprovalReport にしている。
環境変数 APPROVALREPORT_HOME で保存先を変えられる（テスト用）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "ApprovalReport"


def home() -> Path:
    base = os.environ.get("APPROVALREPORT_HOME")
    if base:
        p = Path(base)
    else:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        p = Path(local) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return home() / "examples.db"


def config_path() -> Path:
    return home() / "config.json"


def templates_path() -> Path:
    return home() / "text_templates.json"


def logs_dir() -> Path:
    d = home() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resource(name: str) -> Path:
    """同梱リソース（PyInstaller の単一 exe では展開先の _MEIPASS）。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "resources" / name
