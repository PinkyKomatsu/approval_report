"""用語辞書：英訳に使う日本語 → 英語の辞書。CSV（日本語,英語）でインポート・エクスポートできる。"""
from __future__ import annotations

import csv

from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from core import paths

from ..widgets import AppState, guarded


class GlossaryPage(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        w = self
        l = QVBoxLayout(w)
        l.addWidget(QLabel("英訳に使う用語辞書です（日本語 → 英語）。英語が空欄の語や辞書にない語は、英文に【未翻訳: ○○】と表示されます。"
                           "長い語ほど優先して訳します。"))
        self.gfilter = QLineEdit()
        self.gfilter.setPlaceholderText("検索")
        self.gfilter.textChanged.connect(self._gfilter)
        self.untranslated_only = QCheckBox("英語が空欄の語だけ表示")
        self.untranslated_only.toggled.connect(lambda _: self._gfilter(self.gfilter.text()))
        row = QHBoxLayout()
        row.addWidget(self.gfilter, 1)
        row.addWidget(self.untranslated_only)
        l.addLayout(row)
        self.gl = QTableWidget(0, 2)
        self.gl.setHorizontalHeaderLabels(["日本語", "英語"])
        self.gl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        l.addWidget(self.gl, 1)
        b = QHBoxLayout()
        for text, fn in (("行を追加", self._gl_add), ("CSV をインポート…", self.import_csv), ("CSV をエクスポート…", self.export_csv)):
            x = QPushButton(text)
            x.clicked.connect(lambda _=False, f=fn: f())
            b.addWidget(x)
        b.addStretch(1)
        save = QPushButton("用語辞書を保存")
        save.clicked.connect(lambda: self.save_glossary())
        b.addWidget(save)
        l.addLayout(b)
        info = QLabel(f"データ・設定・ログの保存先：{paths.home()}　（Excel ファイルは読むだけで、変更しません）")
        info.setStyleSheet("color:#666")
        l.addWidget(info)
        self.load_glossary()

    def load_glossary(self):
        self.gl.setRowCount(0)
        for ja, en in sorted(self.state.db.glossary().items()):
            i = self.gl.rowCount()
            self.gl.insertRow(i)
            self.gl.setItem(i, 0, QTableWidgetItem(ja))
            self.gl.setItem(i, 1, QTableWidgetItem(en))

    def _gfilter(self, text):
        for i in range(self.gl.rowCount()):
            ja, en = self.gl.item(i, 0).text(), (self.gl.item(i, 1).text() if self.gl.item(i, 1) else "")
            hide = (bool(text) and text not in ja and text not in en) or (self.untranslated_only.isChecked() and en)
            self.gl.setRowHidden(i, bool(hide))

    def _gl_add(self):
        i = self.gl.rowCount()
        self.gl.insertRow(i)
        self.gl.setItem(i, 0, QTableWidgetItem(""))
        self.gl.setItem(i, 1, QTableWidgetItem(""))
        self.gl.editItem(self.gl.item(i, 0))

    def _gl_terms(self) -> dict:
        out = {}
        for i in range(self.gl.rowCount()):
            ja = (self.gl.item(i, 0) or QTableWidgetItem("")).text().strip()
            if ja:
                out[ja] = (self.gl.item(i, 1) or QTableWidgetItem("")).text().strip()
        return out

    @guarded("用語辞書を保存できませんでした")
    def save_glossary(self):
        terms = self._gl_terms()
        db = self.state.db
        for ja in set(db.glossary()) - set(terms):
            db.delete_term(ja)
        db.upsert_terms(terms, source="編集", keep_existing_en=False)
        self.state.settingsChanged.emit()
        QMessageBox.information(self, "用語辞書", f"保存しました（{len(terms)}語）。英文に反映しました。")

    @guarded("インポートできませんでした")
    def import_csv(self, path: str | None = None):
        path = path or QFileDialog.getOpenFileName(self, "用語辞書（CSV：日本語,英語）", "", "CSV (*.csv)")[0]
        if not path:
            return 0
        terms = {}
        for enc in ("utf-8-sig", "cp932"):
            try:
                with open(path, encoding=enc, newline="") as f:
                    for row in csv.reader(f):
                        if len(row) >= 2 and row[0].strip() and row[0].strip() not in ("日本語", "ja"):
                            terms[row[0].strip()] = row[1].strip()
                break
            except UnicodeDecodeError:
                continue
        self.state.db.upsert_terms(terms, source="インポート", keep_existing_en=False)
        self.load_glossary()
        self.state.settingsChanged.emit()
        return len(terms)

    @guarded("エクスポートできませんでした")
    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "用語辞書を保存", "glossary.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["日本語", "英語"])
            for ja, en in sorted(self.state.db.glossary().items()):
                w.writerow([ja, en])

