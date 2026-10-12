"""メインウィンドウ：6 つの画面をタブで切り替える。検索インデックスの作成など重い処理は QThread で行い、進捗バーを出す。"""
from __future__ import annotations

import logging

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QLabel, QMainWindow, QProgressBar, QTabWidget

from core import paths

from .pages.cases_page import CasesPage
from .pages.format_page import FormatPage
from .pages.matching_page import MatchingPage
from .pages.rules_page import RulesPage
from .pages.search_page import SearchPage
from .pages.settings_page import SettingsPage
from .widgets import AppState, run_in_thread

APP_TITLE = "WBS 報告内容アシスタント（医事会計システム更新）"
log = logging.getLogger("approvalreport")


class MainWindow(QMainWindow):
    def __init__(self, state: AppState | None = None):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1560, 940)
        self.state = state or AppState()
        self.tabs = QTabWidget()
        self.search_page = SearchPage(self.state)
        self.cases_page = CasesPage(self.state)
        self.format_page = FormatPage(self.state)
        self.matching_page = MatchingPage(self.state)
        self.rules_page = RulesPage(self.state)
        self.settings_page = SettingsPage(self.state)
        for page, title in ((self.search_page, "1. トピック検索"), (self.cases_page, "2. 過去事例管理"),
                            (self.format_page, "3. フォーマット・ひな形登録"), (self.matching_page, "4. 照合確認"),
                            (self.rules_page, "5. 条件ルール管理"), (self.settings_page, "6. 設定")):
            self.tabs.addTab(page, title)
        self.setCentralWidget(self.tabs)

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(240)
        self.progress.hide()
        self.busy_label = QLabel("")
        self.statusBar().addPermanentWidget(self.busy_label)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().showMessage(f"保存先：{paths.home()}　／　オフライン動作（外部通信なし）・Excel ファイルは変更しません")

        # キーボードショートカット
        for keys, fn in (("Ctrl+F", self._ctrl_f), ("Ctrl+J", lambda: self.search_page.copy_current("ja")),
                         ("Ctrl+E", lambda: self.search_page.copy_current("en"))):
            sc = QShortcut(QKeySequence(keys), self)
            sc.activated.connect(fn)

        # データが変わったら検索インデックスを作り直す
        for sig in (self.state.casesChanged, self.state.formatsChanged, self.state.settingsChanged):
            sig.connect(self.rebuild_index)
        self.state.rulesChanged.connect(lambda: self.search_page.search() if self.search_page.topic.text().strip() else None)
        self._pending = False
        self._worker = None
        self._gen = 0

    def _ctrl_f(self):
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
            adv = Advisor(state.db, state.cfg, state.templates)
            return adv.build(progress)

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
