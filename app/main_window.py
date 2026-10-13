"""メインウィンドウ：「記載例を探す」「過去事例」「用語辞書」の 3 つの画面。
検索インデックスの作成など重い処理は QThread で行い、進捗バーを出す。"""
from __future__ import annotations

import logging

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QLabel, QMainWindow, QProgressBar, QTabWidget

from core import paths

from .pages.cases_page import CasesPage
from .pages.glossary_page import GlossaryPage
from .pages.search_page import SearchPage
from .widgets import AppState, run_in_thread

APP_TITLE = "WBS 記載例アシスタント（医事会計システム更新）"
log = logging.getLogger("approvalreport")


class MainWindow(QMainWindow):
    def __init__(self, state: AppState | None = None):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1500, 920)
        self.state = state or AppState()
        self.tabs = QTabWidget()
        self.search_page = SearchPage(self.state)
        self.cases_page = CasesPage(self.state)
        self.glossary_page = GlossaryPage(self.state)
        for page, title in ((self.search_page, "記載例を探す"), (self.cases_page, "過去事例"),
                            (self.glossary_page, "用語辞書（英訳）")):
            self.tabs.addTab(page, title)
        self.setCentralWidget(self.tabs)

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(240)
        self.progress.hide()
        self.busy_label = QLabel("")
        self.statusBar().addPermanentWidget(self.busy_label)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().showMessage(f"保存先：{paths.home()}　／　オフライン動作（外部通信なし）・Excel ファイルは変更しません")

        for keys, fn in (("Ctrl+F", self._ctrl_f), ("Ctrl+J", lambda: self.search_page.copy_current("ja")),
                         ("Ctrl+E", lambda: self.search_page.copy_current("en"))):
            QShortcut(QKeySequence(keys), self).activated.connect(fn)

        # 過去事例・用語辞書が変わったら検索インデックスを作り直す
        self.state.casesChanged.connect(self.rebuild_index)
        self.state.settingsChanged.connect(self.rebuild_index)
        self.state.casesChanged.connect(self._first_import)
        self._pending = False
        self._worker = None
        self._gen = 0

    def _ctrl_f(self):
        self.tabs.setCurrentWidget(self.search_page)
        self.search_page.focus_search()

    def _first_import(self):
        """取り込んだら「記載例を探す」に戻る（手順を短くするため）。"""
        if self.tabs.currentWidget() is self.cases_page and self.state.db.cases():
            self.tabs.setCurrentWidget(self.search_page)
            self.search_page.focus_search()

    def rebuild_index(self, sync: bool = False):
        """検索インデックスを作る（形態素解析の結果は DB にキャッシュするので、2 回目以降は新しい行だけ解析する）。
        作成は 1 つずつ行い、作成中に依頼が来たら終わってからもう一度作る。古い依頼の結果は捨てる。"""
        self._gen += 1
        if self._worker is not None and self._worker.isRunning():
            if not sync:
                self._pending = True
                return
            self._worker.wait()
        gen = self._gen
        state = self.state

        def work(progress):
            from core.advisor import Advisor
            return Advisor(state.db, state.cfg, state.templates).build(progress)

        def done(adv):
            if gen == self._gen:
                state.advisor = adv
                log.info("検索インデックスを作成しました（%d行）", len(adv.index.rows))
                state.indexReady.emit()
            if self._pending and not (self._worker is not None and self._worker.isRunning()):
                self._pending = False
                self.rebuild_index()

        if sync:
            done(work(lambda *_: None))
        else:
            self._worker = run_in_thread(self, work, done, "検索インデックスの作成")

    def begin_busy(self, title: str):
        self.busy_label.setText(title + "中…")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.show()

    def set_progress(self, pct: int, text: str = ""):
        self.progress.setValue(max(0, min(100, pct)))
        if text:
            self.busy_label.setText(text)

    def end_busy(self):
        self.progress.hide()
        self.busy_label.setText("")
