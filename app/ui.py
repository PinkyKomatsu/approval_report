"""画面（1 画面）：① 見出し一覧 ② 候補（過去事例） ③ 記入内容。"""
from __future__ import annotations

import re
import shutil
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QDateEdit, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QPushButton, QRadioButton, QSplitter, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import cases as cases_mod
from . import excel_com, ranking
from . import template_parser as tp

PHASES = ["設計", "構築", "テスト"]
DIGITS = re.compile(r"[0-9０-９]")
PREFIX = re.compile(r"^([ 　]*[・●○◆◇■□※\-－]?[ 　]*)")
DEFAULT_PREFIX = "　・"
YELLOW = QColor("#fff3a0")
ROLE = Qt.UserRole


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("工程承認会議 報告書 作成ツール")
        self.resize(1400, 820)
        self.data = cases_mod.data_dir()
        self.fmt_path = self.data / "format.json"
        self.cases_path = self.data / "cases.json"
        self.fmt = tp.load_format(self.fmt_path)
        self.case_data = cases_mod.load_cases(self.cases_path) or {"reports": []}
        self.cands = cases_mod.build_candidates(self.case_data["reports"])
        self.content: dict[str, list[list[str]]] = {}     # セクション → 行 → 列ごとの文
        self.marks: dict[str, list[list[bool]]] = {}      # 黄色表示（候補から入れた日付・数値で、まだ直していない）
        self.items: dict[str, str] = {}
        self.reps: list[tuple[str, str]] = []
        self.current: str | None = None
        self.preview = excel_com.Preview()
        self._build()
        self._reload_all()

    # ------------------------------------------------------------------ 画面
    def _build(self):
        b_tpl = QPushButton("雛形を開く")
        b_tpl.clicked.connect(self.open_template)
        b_imp = QPushButton("過去報告書フォルダを取込")
        b_imp.clicked.connect(self.import_folder)
        self.b_reimp = QPushButton("再取り込み")
        self.b_reimp.clicked.connect(self.reimport)
        self.rb_meeting = QRadioButton("工程承認会議")
        self.rb_report = QRadioButton("報告会")
        self.rb_meeting.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.rb_meeting)
        grp.addButton(self.rb_report)
        self.phase = QComboBox()
        self.phase.setEditable(True)
        self.phase.setMinimumWidth(90)
        self.phase.currentTextChanged.connect(lambda _: self._show_candidates())
        self.date = QDateEdit(QDate.currentDate())
        self.date.setDisplayFormat("yyyy/MM/dd")
        self.date.setCalendarPopup(True)
        row1 = QHBoxLayout()
        for w in (b_tpl, b_imp, self.b_reimp):
            row1.addWidget(w)
        row1.addSpacing(16)
        row1.addWidget(QLabel("会議:"))
        row1.addWidget(self.rb_meeting)
        row1.addWidget(self.rb_report)
        row1.addSpacing(16)
        row1.addWidget(QLabel("工程:"))
        row1.addWidget(self.phase)
        row1.addSpacing(16)
        row1.addWidget(QLabel("作成日:"))
        row1.addWidget(self.date)
        row1.addStretch(1)

        self.topic = QLineEdit()
        self.topic.setPlaceholderText("例：自動精算機IF 未合意、WBS 45/50完了")
        self.topic.textChanged.connect(lambda _: self._show_candidates())
        self.rep_old, self.rep_new = QLineEdit(), QLineEdit()
        self.rep_old.setPlaceholderText("旧語句")
        self.rep_new.setPlaceholderText("新語句")
        b_rep = QPushButton("+")
        b_rep.setFixedWidth(32)
        b_rep.clicked.connect(self.add_replacement)
        self.rep_box = QComboBox()
        self.rep_box.setMinimumWidth(170)
        self.rep_box.setToolTip("登録した置換（③に追加する候補へ自動で適用します）")
        b_rep_del = QPushButton("×")
        b_rep_del.setFixedWidth(32)
        b_rep_del.setToolTip("選んでいる置換を削除")
        b_rep_del.clicked.connect(self.remove_replacement)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("トピック:"))
        row2.addWidget(self.topic, 1)
        row2.addSpacing(12)
        row2.addWidget(QLabel("置換:"))
        row2.addWidget(self.rep_old)
        row2.addWidget(QLabel("→"))
        row2.addWidget(self.rep_new)
        row2.addWidget(b_rep)
        row2.addWidget(self.rep_box)
        row2.addWidget(b_rep_del)

        # ① 見出し一覧
        self.heads = QTreeWidget()
        self.heads.setHeaderLabels(["見出し", "状態"])
        self.heads.setRootIsDecorated(False)
        self.heads.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.heads.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.heads.currentItemChanged.connect(lambda cur, _prev: self._select(cur))
        self.b_pick = QPushButton("選択中のセルを記入先にする")
        self.b_pick.setEnabled(False)
        self.b_pick.clicked.connect(self.pick_cell)
        self.head_note = QLabel("")
        self.head_note.setWordWrap(True)
        self.head_note.setStyleSheet("color:#b00020")
        box1 = QGroupBox("① 見出し一覧")
        l1 = QVBoxLayout(box1)
        l1.addWidget(self.heads, 1)
        l1.addWidget(self.head_note)
        l1.addWidget(self.b_pick)

        # ② 候補
        self.guide = QLabel("")
        self.guide.setWordWrap(True)
        self.guide.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.guide.setStyleSheet("background:#fff8e1; border:1px solid #e0c060; padding:6px")
        self.cand_list = QListWidget()
        self.cand_list.setWordWrap(True)
        self.cand_list.itemDoubleClicked.connect(
            lambda it: it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked))
        b_add = QPushButton("選択した候補を追加")
        b_add.clicked.connect(self.add_candidates)
        b_topic = QPushButton("トピックを追加")
        b_topic.clicked.connect(self.add_topic)
        box2 = QGroupBox("② 候補（過去事例）")
        l2 = QVBoxLayout(box2)
        l2.addWidget(self.guide)
        l2.addWidget(self.cand_list, 1)
        r2 = QHBoxLayout()
        r2.addWidget(b_add)
        r2.addWidget(b_topic)
        l2.addLayout(r2)

        # ③ 記入内容
        self.table = QTableWidget(0, 1)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.itemChanged.connect(self._edited)
        edit_page = QWidget()
        le = QVBoxLayout(edit_page)
        le.setContentsMargins(0, 0, 0, 0)
        le.addWidget(self.table, 1)
        r3 = QHBoxLayout()
        for text, fn in (("↑", lambda: self.move(-1)), ("↓", lambda: self.move(1)), ("削除", self.delete_rows),
                         ("+行追加", self.add_row)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            r3.addWidget(b)
        le.addLayout(r3)
        self.items_page = QWidget()
        self.items_form = QFormLayout(self.items_page)
        self.msg_page = QLabel("")
        self.msg_page.setAlignment(Qt.AlignCenter)
        self.msg_page.setWordWrap(True)
        self.stack = QStackedWidget()
        self.stack.addWidget(edit_page)
        self.stack.addWidget(self.items_page)
        self.stack.addWidget(self.msg_page)
        box3 = QGroupBox("③ 記入内容")
        l3 = QVBoxLayout(box3)
        l3.addWidget(self.stack)

        split = QSplitter(Qt.Horizontal)
        for b in (box1, box2, box3):
            split.addWidget(b)
        split.setSizes([300, 560, 540])

        b_reflect = QPushButton("Excelに反映（プレビュー）")
        b_reflect.setStyleSheet("font-weight:bold; padding:6px 14px")
        b_reflect.clicked.connect(self.reflect)
        b_save = QPushButton("名前を付けて保存")
        b_save.setStyleSheet("padding:6px 14px")
        b_save.clicked.connect(self.save_as)
        row3 = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("color:#555")
        row3.addWidget(self.status, 1)
        row3.addWidget(b_reflect)
        row3.addWidget(b_save)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addLayout(row1)
        lay.addLayout(row2)
        lay.addWidget(split, 1)
        lay.addLayout(row3)
        self.setCentralWidget(central)

    # ------------------------------------------------------------------ 状態
    def _reload_all(self):
        reports = self.case_data.get("reports", [])
        current = self.phase.currentText()
        self.phase.blockSignals(True)
        self.phase.clear()
        self.phase.addItems(PHASES + [p for p in cases_mod.phases(reports) if p not in PHASES])
        self.phase.setCurrentText(current or "構築")
        self.phase.blockSignals(False)
        self.b_reimp.setEnabled(bool(self.case_data.get("folder")))
        tpl = Path(self.fmt["source"]).name if self.fmt else "（未登録）"
        self.status.setText(f"雛形：{tpl}　／　過去報告書：{len(reports)}件　／　保存先：{self.data}")
        self._fill_heads()

    def _sections(self) -> list[dict]:
        return self.fmt["sections"] if self.fmt else []

    def _section(self, key) -> dict | None:
        return next((s for s in self._sections() if s["key"] == key), None)

    def _state_of(self, s: dict) -> str:
        if s["kind"] == "manual":
            return "手入力"
        if s["status"] == tp.NEEDS_CHECK:
            return tp.NEEDS_CHECK
        if any(any(c.strip() for c in row) for row in self.content.get(s["key"], [])):
            return "✔"
        if self.cands.get(s["key"]):
            return "●"
        return ""

    def _items_state(self) -> str:
        if any(v.strip() for v in self.items.values()):
            return "✔"
        reports = self.case_data.get("reports", [])
        if any(cases_mod.item_values(reports, it["key"]) for it in self.fmt["items"]):
            return "●"
        return ""

    def _fill_heads(self):
        keep = self.current
        self.heads.blockSignals(True)
        self.heads.clear()
        if not self.fmt:
            self.heads.blockSignals(False)
            self._select(None)
            return
        entries = [("項目欄", "items", self._items_state(), "")]
        for it in self.fmt["items"]:
            if it["status"] == tp.NEEDS_CHECK:
                entries.append((f"　{it['label']}", f"item:{it['key']}", tp.NEEDS_CHECK, it["note"]))
        if self.fmt["meeting"]["status"] == tp.NEEDS_CHECK:
            entries.append(("　会議種別", "meeting", tp.NEEDS_CHECK, self.fmt["meeting"]["note"]))
        for s in self._sections():
            name = s["name"] + (f"｜{s['right_name']}" if s["kind"] == "pair" and s["right_name"] else "")
            entries.append((name + ("（手入力）" if s["kind"] == "manual" else ""), s["key"], self._state_of(s), s["note"]))
        for name, key, state, note in entries:
            it = QTreeWidgetItem([name, state])
            it.setData(0, ROLE, key)
            if note:
                it.setToolTip(0, note)
            if state == tp.NEEDS_CHECK:
                it.setForeground(1, QBrush(QColor("#b00020")))
            elif state == "手入力":
                for c in (0, 1):
                    it.setForeground(c, QBrush(QColor("#888")))
            self.heads.addTopLevelItem(it)
            if key == keep:
                self.heads.setCurrentItem(it)
        self.heads.blockSignals(False)
        self._select(self.heads.currentItem())

    def _refresh_state(self):
        for i in range(self.heads.topLevelItemCount()):
            it = self.heads.topLevelItem(i)
            key = it.data(0, ROLE)
            s = self._section(key)
            if key == "items":
                it.setText(1, self._items_state())
            elif s:
                it.setText(1, self._state_of(s))

    # ------------------------------------------------------------------ 選択
    def _select(self, item):
        key = item.data(0, ROLE) if item else None
        self.current = key
        self.b_pick.setEnabled(False)
        self.head_note.setText("")
        self.cand_list.clear()
        if not self.fmt:
            self.guide.setText("［雛形を開く］で報告書の雛形（.xlsx）を取り込んでください。")
            self._message("雛形が登録されていません。")
            return
        if key is None:
            self.guide.setText("")
            self._message("① で見出しを選んでください。")
            return
        if key.startswith("item:") or key == "meeting":
            self.guide.setText(self.fmt["items_guide"] or "（記入ガイドなし）")
            self.head_note.setText(item.toolTip(0))
            self.b_pick.setEnabled(True)
            self._message("Excel で開いた雛形の上で記入先のセルを選び、［選択中のセルを記入先にする］を押してください。")
            return
        if key == "items":
            self.guide.setText("記入ガイド：" + (self.fmt["items_guide"] or "（なし）"))
            self._show_items()
            return
        s = self._section(key)
        self.guide.setText("記入ガイド：" + (s["guide"] or "（なし）"))
        if s["kind"] == "manual":
            self._message("この欄は表のため手入力です。ツールでは記入しません（プレビュー後に Excel で入力してください）。")
            return
        if s["status"] == tp.NEEDS_CHECK:
            self.head_note.setText(s["note"] + "。Excel で開いた雛形の上で記入先の先頭セルを選び、下のボタンを押してください。")
            self.b_pick.setEnabled(True)
            if not s.get("start"):
                self._message("記入先が決まっていません。")
                return
        self._show_candidates()
        self._show_table()

    def _message(self, text: str):
        self.msg_page.setText(text)
        self.stack.setCurrentWidget(self.msg_page)

    # ------------------------------------------------------------------ ② 候補
    def _show_candidates(self):
        self.cand_list.clear()
        s = self._section(self.current) if self.current else None
        if not s or s["kind"] == "manual":
            return
        cands = self.cands.get(s["key"], [])
        if not cands:
            self.cand_list.addItem(QListWidgetItem("（過去の記載例がありません）"))
            return
        for r in ranking.rank(cands, self.topic.text(), self.phase.currentText()):
            c = r.cand
            ph = Counter(c.phases).most_common(1)[0][0] if c.phases else "-"
            it = QListWidgetItem(f"{self._apply(c.text)}　（{ph}/{c.count}回）")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            it.setData(ROLE, c)
            it.setToolTip(f"合計点 {r.score:.2f}（トピックとの類似度 {r.sim:.2f}）")
            self.cand_list.addItem(it)

    def _apply(self, text: str) -> str:
        for old, new in self.reps:
            if old:
                text = text.replace(old, new)
        return text

    def add_candidates(self):
        s = self._editable()
        if not s:
            return
        rows, marks = self.content.setdefault(s["key"], []), self.marks.setdefault(s["key"], [])
        for i in range(self.cand_list.count()):
            it = self.cand_list.item(i)
            c = it.data(ROLE)
            if c is None or it.checkState() != Qt.Checked:
                continue
            for row in c.rows:
                cells = [self._apply(v) for v in (row + [""] * len(s["cols"]))[:len(s["cols"])]]
                rows.append(cells)
                marks.append([bool(DIGITS.search(v)) for v in cells])
            it.setCheckState(Qt.Unchecked)
        self._show_table()
        self._refresh_state()

    def prefix_of(self, s: dict) -> str:
        """そのセクションの過去の書き方（行頭の「　・」など）。"""
        cnt = Counter()
        for c in self.cands.get(s["key"], []):
            m = PREFIX.match(c.rows[0][0]) if c.rows and c.rows[0] else None
            if m and m.group(1):
                cnt[m.group(1)] += c.count
        if not cnt:
            for row in s.get("samples", []):
                m = PREFIX.match(row[0]) if row and row[0] else None
                if m and m.group(1):
                    cnt[m.group(1)] += 1
        return cnt.most_common(1)[0][0] if cnt else DEFAULT_PREFIX

    def add_topic(self):
        s = self._editable()
        if not s:
            return
        parts = [p.strip() for p in self.topic.text().split("、") if p.strip()]
        if not parts:
            QMessageBox.information(self, "トピック", "トピックを入力してください。")
            return
        prefix = self.prefix_of(s)
        for p in parts:
            cells = [prefix + p] + [""] * (len(s["cols"]) - 1)
            self.content.setdefault(s["key"], []).append(cells)
            self.marks.setdefault(s["key"], []).append([False] * len(cells))
        self._show_table()
        self._refresh_state()

    def _editable(self) -> dict | None:
        s = self._section(self.current) if self.current else None
        if not s or s["kind"] == "manual" or not s.get("start"):
            QMessageBox.information(self, "記入", "① で記入する見出しを選んでください。")
            return None
        return s

    # ------------------------------------------------------------------ ③ 記入内容
    def _show_table(self):
        s = self._section(self.current)
        self.stack.setCurrentIndex(0)
        self.table.blockSignals(True)
        heads = [s["name"], s["right_name"]] if s["kind"] == "pair" else [s["name"]]
        self.table.setColumnCount(len(s["cols"]))
        self.table.setHorizontalHeaderLabels(heads[:len(s["cols"])])
        rows, marks = self.content.get(s["key"], []), self.marks.get(s["key"], [])
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                it = QTableWidgetItem(v)
                if r < len(marks) and marks[r][c]:
                    it.setBackground(YELLOW)
                    it.setToolTip("過去の候補の日付・数値です。今回の内容に直してください。")
                self.table.setItem(r, c, it)
        self.table.resizeRowsToContents()
        self.table.blockSignals(False)

    def _edited(self, item: QTableWidgetItem):
        key = self.current
        r, c = item.row(), item.column()
        rows = self.content.setdefault(key, [])
        if r < len(rows):
            rows[r][c] = item.text()
            self.marks[key][r][c] = False
            self.table.blockSignals(True)
            item.setBackground(QBrush())
            item.setToolTip("")
            self.table.blockSignals(False)
        self._refresh_state()

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectedIndexes()} or
                      ({self.table.currentRow()} if self.table.currentRow() >= 0 else set()))

    def move(self, d: int):
        s = self._editable()
        if not s:
            return
        rows, marks = self.content.get(s["key"], []), self.marks.get(s["key"], [])
        sel = self._selected_rows()
        if not sel or min(sel) + d < 0 or max(sel) + d >= len(rows):
            return
        for r in (sel if d < 0 else reversed(sel)):
            rows[r], rows[r + d] = rows[r + d], rows[r]
            marks[r], marks[r + d] = marks[r + d], marks[r]
        self._show_table()
        self.table.setCurrentCell(sel[0] + d, 0)

    def delete_rows(self):
        s = self._editable()
        if not s:
            return
        for r in reversed(self._selected_rows()):
            del self.content[s["key"]][r]
            del self.marks[s["key"]][r]
        self._show_table()
        self._refresh_state()

    def add_row(self):
        s = self._editable()
        if not s:
            return
        rows, marks = self.content.setdefault(s["key"], []), self.marks.setdefault(s["key"], [])
        at = self.table.currentRow() + 1 if self.table.currentRow() >= 0 else len(rows)
        rows.insert(at, [""] * len(s["cols"]))
        marks.insert(at, [False] * len(s["cols"]))
        self._show_table()
        self.table.setCurrentCell(at, 0)
        self.table.editItem(self.table.item(at, 0))

    def _show_items(self):
        while self.items_form.rowCount():
            self.items_form.removeRow(0)
        reports = self.case_data.get("reports", [])
        self.items_form.addRow(QLabel("作成日・タイトルの年月・工程・会議種別は、画面上部の値を記入します。\n"
                                      "▼で過去の報告書の値を選べます（置換を適用します）。直接入力もできます。"))
        for it in self.fmt["items"]:
            if it["key"] == "date":
                continue
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems([self._apply(v) for v in cases_mod.item_values(reports, it["key"])])
            combo.setEditText(self.items.get(it["key"], ""))
            combo.setMinimumWidth(320)
            combo.currentTextChanged.connect(lambda text, k=it["key"]: self._item_changed(k, text))
            label = f"{it['label']}（{it['cell']}）" + ("　※要確認" if it["status"] == tp.NEEDS_CHECK else "")
            self.items_form.addRow(label, combo)
        self.stack.setCurrentWidget(self.items_page)

    def _item_changed(self, key: str, text: str):
        self.items[key] = text
        self._refresh_state()

    # ------------------------------------------------------------------ 置換
    def add_replacement(self):
        old, new = self.rep_old.text(), self.rep_new.text()
        if not old:
            return
        self.reps = [(o, n) for o, n in self.reps if o != old] + [(old, new)]
        self._fill_reps()
        self.rep_old.clear()
        self.rep_new.clear()

    def remove_replacement(self):
        i = self.rep_box.currentIndex()
        if 0 <= i < len(self.reps):
            del self.reps[i]
            self._fill_reps()

    def _fill_reps(self):
        self.rep_box.clear()
        self.rep_box.addItems([f"{o} → {n}" for o, n in self.reps])
        self.rep_box.setCurrentIndex(len(self.reps) - 1)
        self._show_candidates()
        if self.current == "items":
            self._show_items()

    # ------------------------------------------------------------------ 雛形・過去報告書
    def open_template(self):
        path, _ = QFileDialog.getOpenFileName(self, "雛形を開く", "", "Excel ブック (*.xlsx *.xlsm)")
        if not path:
            return
        src = Path(path)
        dst = self.data / ("template" + src.suffix.lower())
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            fmt = tp.parse_template(src)
            for old in self.data.glob("template.xls*"):
                old.unlink()
            shutil.copy2(src, dst)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "雛形", f"雛形を読み込めませんでした。\n{e}")
            return
        QApplication.restoreOverrideCursor()
        fmt.update(source=str(src), template=str(dst))
        tp.save_format(fmt, self.fmt_path)
        self.fmt, self.content, self.marks, self.current = fmt, {}, {}, None
        self._reload_all()
        n_check = sum(1 for it in fmt["items"] if it["status"] == tp.NEEDS_CHECK) + \
            sum(1 for s in fmt["sections"] if s["status"] == tp.NEEDS_CHECK)
        n_manual = sum(1 for s in fmt["sections"] if s["kind"] == "manual")
        msg = (f"シート「{fmt['sheet']}」を解析しました。\n文章セクション {len(fmt['sections']) - n_manual} 件、"
               f"手入力の表 {n_manual} 件、要確認 {n_check} 件。")
        if fmt["warnings"]:
            msg += "\n\n" + "\n".join(fmt["warnings"])
        QMessageBox.information(self, "雛形", msg)

    def import_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "過去報告書のフォルダ")
        if folder:
            self._import(folder)

    def reimport(self):
        if self.case_data.get("folder"):
            self._import(self.case_data["folder"])

    def _import(self, folder):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            data = cases_mod.import_folder(folder)
        finally:
            QApplication.restoreOverrideCursor()
        cases_mod.save_cases(data, self.cases_path)
        self.case_data = data
        self.cands = cases_mod.build_candidates(data["reports"])
        self._reload_all()
        msg = f"{len(data['reports'])} 件の報告書を取り込みました。"
        if data["errors"]:
            msg += "\n\n読めなかったファイル：\n" + "\n".join(data["errors"][:20])
        QMessageBox.information(self, "過去報告書", msg)

    def pick_cell(self):
        key = self.current
        if not self.fmt or not key:
            return
        got = excel_com.active_cell()
        template = Path(self.fmt["template"])
        if got is None:
            if QMessageBox.question(self, "記入先の指定",
                                    "Excel で雛形が開かれていません。雛形を Excel で開きますか？\n"
                                    "開いたら記入先のセルを選び、もう一度ボタンを押してください。") == QMessageBox.Yes:
                excel_com.open_visible(template)
            return
        book, sheet, row, col = got
        if Path(book).resolve() != template.resolve() or sheet != self.fmt["sheet"]:
            QMessageBox.warning(self, "記入先の指定", f"雛形（{template}）のシート「{self.fmt['sheet']}」でセルを選んでください。\n"
                                                    f"いま選ばれているのは「{Path(book).name}」の「{sheet}」です。")
            return
        tp.set_target(self.fmt, key, row, col)
        tp.save_format(self.fmt, self.fmt_path)
        self.status.setText(f"記入先を {tp.addr(row, col)} にしました。")
        self._fill_heads()

    # ------------------------------------------------------------------ Excel
    def _content(self) -> dict:
        sections = {}
        for key, rows in self.content.items():
            rows = [list(r) for r in rows]
            while rows and not any(c.strip() for c in rows[-1]):
                rows.pop()
            if rows:
                sections[key] = rows
        return {"date": self.date.date().toPython(), "phase": self.phase.currentText().strip(),
                "meeting": "工程承認会議" if self.rb_meeting.isChecked() else "報告会",
                "items": {k: v.strip() for k, v in self.items.items() if v.strip()}, "sections": sections}

    def reflect(self) -> bool:
        if not self.fmt:
            QMessageBox.information(self, "Excel", "先に［雛形を開く］で雛形を取り込んでください。")
            return False
        if self.preview.alive() and QMessageBox.question(
                self, "Excel", "前回のプレビューを閉じて、作り直します。Excel 上で直した内容は失われます。よろしいですか？") != QMessageBox.Yes:
            return False
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.preview.reflect(self.fmt["template"], self.fmt, self._content())
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Excel", f"Excel に反映できませんでした。\n{e}")
            return False
        QApplication.restoreOverrideCursor()
        self.status.setText("Excel に反映しました。Excel 上で直接修正してもかまいません。保存は［名前を付けて保存］で行います。")
        return True

    def default_name(self) -> str:
        d = self.date.date().toPython()
        phase = re.sub(r'[\\/:*?"<>|]', "_", self.phase.currentText().strip())
        return f"{d:%Y%m%d}_工程承認会議_{phase}.xlsx"

    def save_as(self):
        if not self.preview.alive() and not self.reflect():
            return
        start = str(Path(self.fmt.get("source") or self.data).parent / self.default_name())
        path, _ = QFileDialog.getSaveFileName(self, "名前を付けて保存", start, "Excel ブック (*.xlsx)")
        if not path:
            return
        try:
            self.preview.save_as(path)
        except Exception as e:
            QMessageBox.warning(self, "保存", f"保存できませんでした。\n{e}")
            return
        self.status.setText(f"保存しました：{path}")
