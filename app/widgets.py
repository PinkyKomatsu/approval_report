"""画面で共有する部品：アプリの状態・QThread ワーカー・例外表示。"""
from __future__ import annotations

import functools
import logging
import traceback

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

from core import config as config_mod, workflow
from core.advisor import Advisor
from core.db import Database

log = logging.getLogger("approvalreport")



def error_message(e: BaseException) -> str:
    """利用者向けの日本語メッセージ。"""
    if isinstance(e, PermissionError):
        return "ファイルを開けません。ほかのアプリで使用中でないか、アクセス権があるか確認してください。"
    if isinstance(e, FileNotFoundError):
        return f"ファイルが見つかりません：{getattr(e, 'filename', '') or e}"
    if e.__class__.__name__ in ("BadZipFile", "InvalidFileException"):
        return "Excel ファイルとして読み込めません。ファイルが壊れていないか確認してください。"
    msg = str(e)
    return msg if msg and any("぀" <= ch <= "鿿" for ch in msg) else f"処理中にエラーが発生しました（{e.__class__.__name__}）。"


def show_error(parent, title: str, e: BaseException) -> None:
    # ログには例外の種類と場所だけを書き、メッセージ（セルの内容を含み得る）は書かない
    tb = traceback.extract_tb(e.__traceback__)
    where = " <- ".join(f"{f.filename.rsplit(chr(92), 1)[-1]}:{f.lineno}" for f in reversed(tb[-4:]))
    log.error("%s: %s at %s", title, e.__class__.__name__, where)
    QMessageBox.critical(parent, title, error_message(e))


def guarded(title: str):
    """スロットの例外を捕捉して日本語のダイアログを出すデコレーター。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            try:
                return fn(self, *args, **kwargs)
            except Exception as e:  # すべて捕捉する
                show_error(self, title, e)
                return None
        return wrapper
    return deco


class AppState(QObject):
    casesChanged = Signal()
    settingsChanged = Signal()
    indexReady = Signal()

    def __init__(self, db: Database | None = None):
        super().__init__()
        self.db = db or Database()
        workflow.seed_glossary(self.db)
        self.cfg = config_mod.load()
        self.templates = config_mod.load_text_templates()
        self.advisor = Advisor(self.db, self.cfg, self.templates)
        self.values: dict[str, str] = dict(self.cfg.get("placeholder_values") or {})   # {{日付}} などに入れた今回の値

    def save_values(self):
        self.cfg["placeholder_values"] = self.values
        config_mod.save(self.cfg)


class Worker(QThread):
    """重い処理をバックグラウンドで実行する。fn(progress) の progress(割合 0-100, 説明) で進捗を知らせる。"""
    progressed = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            result = self.fn(lambda pct, text="": self.progressed.emit(int(pct), text))
        except Exception as e:
            self.failed.emit(e)
        else:
            self.succeeded.emit(result)


def run_in_thread(owner, fn, on_done, title: str):
    """Worker を起動し、メインウィンドウの進捗バーに進捗を出す。"""
    win = owner.window()
    w = Worker(fn, owner)
    owner._workers = getattr(owner, "_workers", set())
    owner._workers.add(w)
    if hasattr(win, "begin_busy"):
        win.begin_busy(title)
        w.progressed.connect(win.set_progress)

    def done(result):
        owner._workers.discard(w)
        if hasattr(win, "end_busy"):
            win.end_busy()
        try:
            on_done(result)
        except Exception as e:
            show_error(owner, title, e)

    def fail(e):
        owner._workers.discard(w)
        if hasattr(win, "end_busy"):
            win.end_busy()
        show_error(owner, title, e)

    w.succeeded.connect(done)
    w.failed.connect(fail)
    w.start()
    return w


def ro_item(text, data=None) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    if data is not None:
        item.setData(Qt.UserRole, data)
    return item
