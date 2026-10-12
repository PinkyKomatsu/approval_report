# -*- mode: python ; coding: utf-8 -*-
# PyInstaller の設定：単一 exe（--onefile 相当）・ウィンドウアプリ（--windowed 相当）
#   .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean build.spec   → dist\ApprovalReport.exe
# resources（初期設定・定型文・用語辞書）と Janome の辞書は exe に同梱し、実行時は一時展開先（_MEIPASS）から読む。
# データ・設定・ログは %LOCALAPPDATA%\ApprovalReport\ に保存するため、exe の置き場所は書き込めなくてもよい。
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    # Janome は辞書（sysdic の .py）をファイルとして mmap で読むので、ソースのまま同梱する
    datas=[("resources", "resources")] + collect_data_files("janome", include_py_files=True),
    hiddenimports=collect_submodules("janome") + ["lxml._elementpath"],
    excludes=[
        "tkinter", "PIL", "pytest", "win32com", "pythoncom",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick",
        "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtPdf", "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtSql", "PySide6.QtTest",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ApprovalReport",
    console=False,
    disable_windowed_traceback=False,
    upx=False,
    runtime_tmpdir=None,
)
