"""過去事例：実用WBSの取込（ドラッグ＆ドロップ対応）・一覧・名前の変更・削除。列は見出しから自動で判別する。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QVBoxLayout, QWidget)

from core import workflow
from core.models import field_label

from ..widgets import AppState, error_message, guarded, ro_item, run_in_thread


class DropArea(QLabel):
    def __init__(self, on_files, parent=None):
        super().__init__("ここに過去事例の実用WBS（.xlsx / .xlsm）をドラッグ＆ドロップ（複数可）", parent)
        self.on_files = on_files
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(90)
        self.setStyleSheet("border:2px dashed #8aa; color:#557; background:#f7fafc; font-size:14px")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.on_files([u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()])


class CasesPage(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        top = QHBoxLayout()
        for text, fn in (("ファイルを追加…", self.choose_files), ("名前を変更…", self.rename_selected),
                         ("選んだ事例を削除", self.delete_selected)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, f=fn: f())
            top.addWidget(b)
        top.addStretch(1)
        self.count = QLabel("")
        top.addWidget(self.count)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["事例名", "ファイル", "取込日時", "行数", "読み取った欄"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        layout = QVBoxLayout(self)
        layout.addWidget(DropArea(self.import_files))
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(QLabel("取込ログ"))
        layout.addWidget(self.log)
        state.casesChanged.connect(self.refresh)
        self.refresh()

    def refresh(self):
        counts = self.state.db.row_counts()
        self.table.setRowCount(0)
        for c in self.state.db.cases():
            i = self.table.rowCount()
            self.table.insertRow(i)
            cols = "、".join(f"{col}:{field_label(m)}" for col, m in (c.columns or {}).items())
            for col, v in enumerate((c.name, Path(c.source).name, c.imported.replace("T", " "), counts.get(c.id, 0), cols)):
                self.table.setItem(i, col, ro_item(v, c.id))
        self.count.setText(f"{self.table.rowCount()}件")

    def _selected_ids(self) -> list[int]:
        return sorted({self.table.item(i.row(), 0).data(Qt.UserRole) for i in self.table.selectedIndexes()})

    @guarded("名前を変更できませんでした")
    def rename_selected(self):
        ids = self._selected_ids()
        if not ids:
            return
        case = next(c for c in self.state.db.cases() if c.id == ids[0])
        name, ok = QInputDialog.getText(self, "名前を変更", "事例名（記載例の根拠に表示します）", text=case.name)
        if ok and name.strip():
            self.state.db.rename_case(case.id, name.strip())
            self.state.casesChanged.emit()

    @guarded("削除できませんでした")
    def delete_selected(self):
        ids = self._selected_ids()
        if not ids or QMessageBox.question(self, "削除の確認", f"{len(ids)}件の事例を削除しますか？") != QMessageBox.Yes:
            return
        for cid in ids:
            self.state.db.delete_case(cid)
        self.state.casesChanged.emit()

    @guarded("取り込めませんでした")
    def choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "過去事例の実用WBSを選択", "", "Excel ブック (*.xlsx *.xlsm)")
        if files:
            self.import_files(files)

    def import_files(self, files, sync: bool = False):
        state = self.state

        def work(progress):
            log, dups = [], []
            for n, f in enumerate(files):
                progress(n * 100 / max(1, len(files)), Path(f).name)
                try:
                    workflow.import_case(state.db, f, state.cfg)
                    log.append(f"取込：{Path(f).name}")
                except workflow.DuplicateCase as e:
                    dups.append(f)
                    log.append(f"警告：{e}")
                except Exception as e:
                    log.append(f"失敗：{error_message(e)}")
            return log, dups

        def done(result):
            log, dups = result
            for line in log:
                self.log.appendPlainText(line)
            if dups:
                names = "\n".join(Path(d).name for d in dups)
                if QMessageBox.warning(self, "二重取込", f"次のファイルは取込済みです（内容が同じファイル）。\n{names}\n\nそれでも取り込みますか？",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                    for d in dups:
                        workflow.import_case(state.db, d, state.cfg, allow_duplicate=True)
                        self.log.appendPlainText(f"取込（二重）：{Path(d).name}")
            state.casesChanged.emit()

        if sync:
            done(work(lambda *_: None))
        else:
            run_in_thread(self, work, done, "過去事例の取込")
