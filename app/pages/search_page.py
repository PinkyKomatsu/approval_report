"""1. トピック検索（メイン画面）。

上部：案件・トピック（作業項目名を補完）・属性フィルター・プレースホルダーの値・全件コピー
左  ：作業項目リスト（必要性つき）
中央：欄ごとの日本語／英語の推奨記載例とコピーボタン（他の記載例との入れ替え・編集・定型文として保存）
右  ：フォーマットのプレビュー（記載先のハイライト）と転記チェックリスト
"""
from __future__ import annotations

import re

from PySide6.QtCore import QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QSplitter, QTabWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from core import copytext, workflow
from core.advisor import FILTER_OFF, FILTER_ONLY, FILTER_WEIGHT, FieldResult, ItemResult
from core.models import FIELDS, REQUIRED, UNNEEDED, Project
from core.placeholders import keys_in, substitute
from excel_io import inspect

from ..widgets import AppState, AttrsEditor, SheetGrid, guarded

DECISION_COLORS = {REQUIRED: "#1f7a3d", UNNEEDED: "#888888"}


def _pct(rate) -> str:
    return "-" if rate is None else f"{int(rate * 100 + 0.5)}%"


class ProjectDialog(QDialog):
    def __init__(self, cfg: dict, project: Project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("案件（今回の案件の名前と属性）")
        lay = QVBoxLayout(self)
        self.name = QLineEdit(project.name)
        lay.addWidget(QLabel("案件名"))
        lay.addWidget(self.name)
        lay.addWidget(QLabel("案件属性（任意。属性フィルター・ルールの条件に使います）"))
        self.attrs = AttrsEditor(cfg)
        self.attrs.set_attrs(project.attrs)
        lay.addWidget(self.attrs)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class FieldCard(QFrame):
    """1 つの欄：日本語／英語の文、コピー、他の記載例、編集、定型文として保存。"""
    focused = Signal(str)
    edited = Signal(str, str, str)          # 欄, 日本語, 英語
    copied = Signal(str, str)               # 言語, 欄

    def __init__(self, page: "SearchPage", item: ItemResult, fr: FieldResult, parent=None):
        super().__init__(parent)
        self.page, self.item, self.fr = page, item, fr
        self.field = fr.field
        self.current = fr.rep                 # 表示中の記載例
        self.en_manual = False
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("FieldCard{background:#fbfcfe}")
        g = QGridLayout(self)
        col = fr.constraint.get("col")
        title = QLabel(f"<b>{FIELDS.get(self.field, self.field)}</b>" + (f"（{col}列）" if col else ""))
        g.addWidget(title, 0, 0)
        self.info = QLabel("")
        self.info.setStyleSheet("color:#555")
        self.info.setWordWrap(True)
        g.addWidget(self.info, 0, 1)
        self.ja = QPlainTextEdit()
        self.en = QPlainTextEdit()
        for w in (self.ja, self.en):
            w.setMaximumHeight(84)
            w.installEventFilter(self)
        g.addWidget(self.ja, 1, 0)
        g.addWidget(self.en, 1, 1)
        bj, be = QPushButton("日本語コピー"), QPushButton("英語コピー")
        bj.clicked.connect(lambda: self.copy("ja"))
        be.clicked.connect(lambda: self.copy("en"))
        rj, re_ = QHBoxLayout(), QHBoxLayout()
        rj.addWidget(bj)
        self.others = QComboBox()
        self.others.setToolTip("他の記載例を選ぶと、表示中の文と入れ替えます")
        self.others.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.others.setMinimumContentsLength(8)
        self.others.activated.connect(self._choose_other)
        rj.addWidget(self.others, 1)
        re_.addWidget(be)
        save = QPushButton("定型文として保存")
        save.setToolTip("表示中の文を「自分の定型文」として保存し、次回以降の候補に含めます")
        save.clicked.connect(self.save_phrase)
        re_.addWidget(save)
        reset = QPushButton("推奨に戻す")
        reset.clicked.connect(self.reset)
        re_.addWidget(reset)
        g.addLayout(rj, 2, 0)
        g.addLayout(re_, 2, 1)
        self.source = QLabel("")
        self.source.setStyleSheet("color:#666")
        self.source.setWordWrap(True)
        g.addWidget(self.source, 3, 0, 1, 2)
        self.warn = QLabel("")
        self.warn.setStyleSheet("color:#b00020")
        self.warn.setWordWrap(True)
        g.addWidget(self.warn, 4, 0, 1, 2)
        self._loading = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._ja_settled)
        self.ja.textChanged.connect(lambda: None if self._loading else self._timer.start())
        self.en.textChanged.connect(self._en_changed)
        self._fill_others()
        self.refresh()

    # ---- 表示 ----
    def _fill_others(self):
        self.others.clear()
        n = len(self.fr.others)
        self.others.addItem(f"他の記載例（{n}件）" if n else "他の記載例なし")
        for ex in self.fr.others:
            text = ex.template.replace("\n", " ")
            self.others.addItem(f"{text[:60]}{'…' if len(text) > 60 else ''}　［{ex.label()}］")

    def refresh(self):
        """案件の値（プレースホルダー）・編集内容を反映して表示し直す。"""
        values = self.page.state.project.values
        edit = self.page.edits.get((self.item.key, self.field))
        self._loading = True
        if edit:
            ja, en = substitute(edit[0], values), substitute(edit[1], self.page.advisor.values_en(values))
            if not en:
                en = substitute(self.page.advisor.translate(edit[0])[0], self.page.advisor.values_en(values))
            self.en_manual = bool(edit[1])
        else:
            ja, en, _un = self.page.advisor.render(self.current, values)
            self.en_manual = False
        self.ja.setPlainText(ja)
        self.en.setPlainText(en)
        self._loading = False
        self._update_info()

    def _update_info(self):
        c, ex = self.fr.constraint, self.current
        parts = []
        if c.get("allowed"):
            parts.append("プルダウン：" + "／".join(c["allowed"]))
        if self.fr.limit:
            parts.append(f"文字数の目安：{self.fr.limit}字")
        if c.get("number_format") and self.field in ("plan_date", "actual_date"):
            parts.append(f"日付形式：{c['number_format']}")
        if c.get("merged"):
            parts.append(f"結合セル {c['merged']}")
        self.info.setText("　".join(parts))
        warns = []
        text = self.ja.toPlainText()
        if self.fr.limit and len(text) > self.fr.limit:
            warns.append(f"文字数が目安を超えています（{len(text)}字／目安 {self.fr.limit}字）")
        if c.get("allowed"):
            _v, note = self.page.advisor.convert_allowed(text.strip(), c["allowed"])
            if note:
                warns.append(note)
        un = re.findall(r"【未翻訳: ([^】]*)】", self.en.toPlainText())
        if un:
            warns.append(f"未翻訳の語 {len(un)}件：" + "、".join(un))
        left = keys_in(text)
        if left:
            warns.append("差し替えていない値：" + " ".join("{{" + k + "}}" for k in left))
        self.warn.setText("\n".join(warns))
        if ex is None:
            self.source.setText("根拠：過去事例にこの欄の記載がありません")
        else:
            self.source.setText(f"根拠：{ex.label()}" + (f"（{ex.count}件の事例で同じ記載）" if ex.count > 1 else ""))

    # ---- 値 ----
    def texts(self) -> tuple[str, str]:
        """コピーする文（プルダウンの欄は許可値に変換する）。"""
        ja, en = self.ja.toPlainText().strip(), self.en.toPlainText().strip()
        allowed = self.fr.constraint.get("allowed")
        if allowed and ja:
            conv, _note = self.page.advisor.convert_allowed(ja, allowed)
            if conv != ja:
                ja, en = conv, self.page.advisor.translate(conv)[0]
        return ja, en

    def copy(self, lang: str):
        ja, en = self.texts()
        QGuiApplication.clipboard().setText(ja if lang == "ja" else en)
        self.page.notify(f"コピーしました（{'日本語' if lang == 'ja' else '英語'}・1欄）")

    # ---- 編集 ----
    def eventFilter(self, obj, ev):
        if ev.type() == ev.Type.FocusIn:
            self.page.current_field = self.field
            self.focused.emit(self.field)
        return False

    def _ja_settled(self):
        ja = self.ja.toPlainText()
        en = self.en.toPlainText() if self.en_manual else ""
        if not self.en_manual:
            self._loading = True
            self.en.setPlainText(self.page.advisor.translate(ja)[0])
            self._loading = False
        self.page.save_edit(self.item.key, self.field, ja, en)
        self._update_info()

    def _en_changed(self):
        if self._loading:
            return
        self.en_manual = True
        self.page.save_edit(self.item.key, self.field, self.ja.toPlainText(), self.en.toPlainText())
        self._update_info()

    def _choose_other(self, index: int):
        if index <= 0:
            return
        ex = self.fr.others[index - 1]
        # 推奨と入れ替える
        old = self.current
        self.fr.others[index - 1] = old
        self.current = ex
        if old is None:
            self.fr.others.pop(index - 1)
        self.page.drop_edit(self.item.key, self.field)
        self.page.save_edit(self.item.key, self.field, ex.template, ex.en_template)
        self._fill_others()
        self.refresh()

    def reset(self):
        self.current = self.fr.rep
        self.page.drop_edit(self.item.key, self.field)
        self.refresh()

    @guarded("定型文を保存できませんでした")
    def save_phrase(self):
        ja = self.ja.toPlainText().strip()
        if not ja:
            return
        en = self.en.toPlainText().strip()
        self.page.state.db.add_phrase(self.item.key, self.field, ja, en if "【未翻訳" not in en else "")
        self.page.notify("自分の定型文として保存しました（次回の検索から候補に含めます）")


class SearchPage(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.results: list[ItemResult] = []
        self.cards: dict[str, FieldCard] = {}
        self.current: ItemResult | None = None
        self.current_field: str | None = None
        self.edits: dict = {}
        self.picked: str | None = None

        # ---- 上部 ----
        self.project_box = QComboBox()
        self.project_box.setMinimumWidth(180)
        self.project_box.activated.connect(lambda _i: self._project_chosen())
        b_new = QPushButton("新規案件…")
        b_new.clicked.connect(lambda: self.new_project())
        b_edit = QPushButton("案件の属性…")
        b_edit.clicked.connect(lambda: self.edit_project())
        self.topic = QLineEdit()
        self.topic.setPlaceholderText("トピック（例：マスタ移行、連携テスト、診療報酬改定対応）")
        self.topic.returnPressed.connect(lambda: self.search())
        self.completer = QCompleter([], self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.topic.setCompleter(self.completer)
        b_search = QPushButton("検索（Ctrl+F）")
        b_search.clicked.connect(lambda: self.search())
        self.mode = QComboBox()
        for label, m in (("属性：使わない", FILTER_OFF), ("属性で重み付け", FILTER_WEIGHT), ("属性で絞り込み", FILTER_ONLY)):
            self.mode.addItem(label, m)
        self.mode.setToolTip("「案件の属性」に入力した属性で、過去事例を絞り込む／属性が近い事例の重みを上げる")
        self.mode.activated.connect(lambda _i: self.search() if self.topic.text().strip() else None)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("案件"))
        row1.addWidget(self.project_box)
        row1.addWidget(b_new)
        row1.addWidget(b_edit)
        row1.addSpacing(12)
        row1.addWidget(QLabel("トピック"))
        row1.addWidget(self.topic, 1)
        row1.addWidget(b_search)
        row1.addWidget(self.mode)

        self.ph_box = QWidget()
        self.ph_layout = QHBoxLayout(self.ph_box)
        self.ph_layout.setContentsMargins(0, 0, 0, 0)
        self.ph_edits: dict[str, QLineEdit] = {}
        self.copy_mode = QComboBox()
        self.copy_mode.addItem("列位置をそろえる（数式列・結合セルは空欄）", copytext.ALIGN)
        self.copy_mode.addItem("対象欄のみ", copytext.FIELDS_ONLY)
        self.copy_mode.setCurrentIndex(max(0, self.copy_mode.findData(state.cfg.get("copy", {}).get("mode", "align"))))
        b_all_ja, b_all_en = QPushButton("全件コピー（日本語）"), QPushButton("全件コピー（英語）")
        b_all_ja.clicked.connect(lambda: self.copy_all("ja"))
        b_all_en.clicked.connect(lambda: self.copy_all("en"))
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("今回の案件の値"))
        ph_scroll = QScrollArea()
        ph_scroll.setWidgetResizable(True)
        ph_scroll.setFrameShape(QFrame.NoFrame)
        ph_scroll.setFixedHeight(40)
        ph_scroll.setWidget(self.ph_box)
        row2.addWidget(ph_scroll, 1)
        row2.addWidget(QLabel("コピー形式"))
        row2.addWidget(self.copy_mode)
        row2.addWidget(b_all_ja)
        row2.addWidget(b_all_en)
        self.untranslated = QLabel("")
        self.untranslated.setStyleSheet("color:#b00020")

        # ---- 左：作業項目 ----
        self.items = QTreeWidget()
        self.items.setHeaderLabels(["作業項目", "必要性", "採用率", "点"])
        self.items.setRootIsDecorated(False)
        self.items.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
            self.items.header().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.items.currentItemChanged.connect(lambda cur, _p: self._item_selected(cur))

        # ---- 中央：欄ごとの記載例 ----
        self.head = QLabel("トピックを入力して検索してください。")
        self.head.setWordWrap(True)
        self.head.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.reason = QLabel("")
        self.reason.setWordWrap(True)
        self.reason.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.reason.setStyleSheet("color:#444")
        b_row_ja, b_row_en = QPushButton("行コピー（日本語）"), QPushButton("行コピー（英語）")
        b_row_ja.setToolTip("フォーマットの列順のタブ区切り。Excel の行頭セルに貼ると 1 行分が埋まります")
        b_row_ja.clicked.connect(lambda: self.copy_row("ja"))
        b_row_en.clicked.connect(lambda: self.copy_row("en"))
        hb = QHBoxLayout()
        hb.addWidget(QLabel("<b>日本語</b>"), 1)
        hb.addWidget(QLabel("<b>English</b>"), 1)
        hb.addWidget(b_row_ja)
        hb.addWidget(b_row_en)
        self.cards_box = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_box)
        self.cards_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.cards_box)
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.head)
        cl.addWidget(self.reason)
        cl.addLayout(hb)
        cl.addWidget(scroll, 1)

        # ---- 右：プレビュー・チェックリスト ----
        self.grid = SheetGrid(max_rows=400)
        self.grid.cellPicked.connect(self._cell_picked)
        self.target_label = QLabel("")
        self.target_label.setWordWrap(True)
        b_pick = QPushButton("このセルを記載先にする")
        b_pick.clicked.connect(lambda: self.apply_pick())
        prev = QWidget()
        pl = QVBoxLayout(prev)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(self.target_label)
        pl.addWidget(self.grid, 1)
        pr = QHBoxLayout()
        self.pick_label = QLabel("")
        pr.addWidget(self.pick_label, 1)
        pr.addWidget(b_pick)
        pl.addLayout(pr)
        self.check = QTreeWidget()
        self.check.setHeaderLabels(["作業項目", "欄", "転記済み"])
        self.check.setRootIsDecorated(False)
        self.check.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.check.itemChanged.connect(self._check_changed)
        self.check_label = QLabel("")
        chk = QWidget()
        kl = QVBoxLayout(chk)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.addWidget(self.check_label)
        kl.addWidget(self.check, 1)
        self.right = QTabWidget()
        self.right.addTab(prev, "記載先のプレビュー")
        self.right.addTab(chk, "転記チェックリスト")

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.items)
        split.addWidget(center)
        split.addWidget(self.right)
        split.setSizes([360, 720, 480])
        layout = QVBoxLayout(self)
        layout.addLayout(row1)
        layout.addLayout(row2)
        layout.addWidget(self.untranslated)
        layout.addWidget(split, 1)

        state.indexReady.connect(self._index_ready)
        state.projectChanged.connect(self._project_changed)
        state.formatsChanged.connect(self._load_preview)
        self._fill_projects()
        self._load_preview()

    @property
    def advisor(self):
        return self.state.advisor

    def notify(self, text: str):
        win = self.window()
        if hasattr(win, "statusBar"):
            win.statusBar().showMessage(text, 5000)

    # ------------------------------------------------------------------ 案件
    def _fill_projects(self):
        self.project_box.blockSignals(True)
        self.project_box.clear()
        for p in self.state.db.projects():
            self.project_box.addItem(p.name, p.id)
        self.project_box.setCurrentIndex(max(0, self.project_box.findData(self.state.project.id)))
        self.project_box.blockSignals(False)
        self.edits = self.state.db.edits(self.state.project.id)

    def _project_chosen(self):
        pid = self.project_box.currentData()
        if pid and pid != self.state.project.id:
            self.state.set_project(pid)

    def _project_changed(self):
        self._fill_projects()
        self._fill_placeholders()
        if self.topic.text().strip():
            self.search()
        self._fill_checklist()

    @guarded("案件を作成できませんでした")
    def new_project(self):
        name, ok = QInputDialog.getText(self, "新規案件", "案件名")
        if ok and name.strip():
            pid = self.state.db.save_project(Project(None, name.strip()))
            self.state.set_project(pid)

    @guarded("案件を保存できませんでした")
    def edit_project(self):
        p = self.state.project
        d = ProjectDialog(self.state.cfg, p, self)
        if d.exec() == QDialog.Accepted:
            p.name = d.name.text().strip() or p.name
            p.attrs = d.attrs.attrs()
            self.state.save_project()
            self.state.projectChanged.emit()

    # ------------------------------------------------------------------ 検索
    def _index_ready(self):
        self.completer.setModel(QStringListModel(self.advisor.item_names(), self.completer))
        self._load_preview()
        if self.topic.text().strip():
            self.search()

    @guarded("検索できませんでした")
    def search(self):
        if not self.advisor.ready:
            self.head.setText("検索インデックスを準備しています。しばらくお待ちください。")
            return
        topic = self.topic.text().strip()
        if not topic:
            return
        self.results = self.advisor.search(topic, self.state.project.attrs, self.mode.currentData())
        self.items.blockSignals(True)
        self.items.clear()
        for r in self.results:
            name = r.name + ("（ひな形外）" if r.is_extra else "")
            it = QTreeWidgetItem([("　" if r.wbs_no.count(".") else "") + (f"{r.wbs_no} " if r.wbs_no else "") + name,
                                  r.necessity.decision, _pct(r.necessity.rate), f"{r.score:.2f}"])
            it.setData(0, Qt.UserRole, r.key)
            color = DECISION_COLORS.get(r.necessity.decision)
            if color:
                it.setForeground(1, QBrush(QColor(color)))
            if r.necessity.rule:
                it.setToolTip(1, f"ルール『{r.necessity.rule.name}』を適用")
            self.items.addTopLevelItem(it)
        self.items.blockSignals(False)
        if self.results:
            self.items.setCurrentItem(self.items.topLevelItem(0))
        else:
            self.current = None
            self._clear_cards()
            self.head.setText(f"「{topic}」に関連する過去事例は見つかりませんでした。")
            self.reason.setText("")
        self._fill_placeholders()
        self._fill_checklist()
        self._update_untranslated()
        self.notify(f"{len(self.results)}件の作業項目が見つかりました")

    def _item_selected(self, it):
        key = it.data(0, Qt.UserRole) if it else None
        self.current = next((r for r in self.results if r.key == key), None)
        self._show_item()

    def _clear_cards(self):
        for c in self.cards.values():
            c.setParent(None)
            c.deleteLater()
        self.cards.clear()

    def _show_item(self):
        self._clear_cards()
        r = self.current
        if r is None:
            return
        n = r.necessity
        self.head.setText(f"<b>{r.wbs_no} {r.name}</b>　{r.phase}　／　{r.name_en}<br>"
                          f"必要性：<b>{n.decision}</b>（採用率 {_pct(n.rate)}・過去{n.total}件中{len(n.adopted)}件）"
                          + (f"　ルール『{n.rule.name}』を適用（統計では{n.stat}）" if n.rule else ""))
        self.reason.setText(f"{r.reason_ja}<br>{r.reason_en}<br>根拠：" + ("、".join(r.cases) or "（記載のある事例なし）"))
        for f, fr in r.fields.items():
            card = FieldCard(self, r, fr)
            card.focused.connect(lambda field: self._highlight())
            self.cards[f] = card
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        self.current_field = next(iter(r.fields), None)
        self._highlight()

    def _update_untranslated(self):
        un = set()
        for r in self.results:
            for fr in r.fields.values():
                if fr.rep is not None and not fr.rep.en_template:
                    un.update(self.advisor.translate(fr.rep.template)[1])
            un.update(self.advisor.translate(r.name)[1])
        self.untranslated.setText(f"未翻訳の語が {len(un)}件あります（英文の【未翻訳: …】）。「6. 設定」の用語辞書で英語を登録できます："
                                  + "、".join(sorted(un)[:15]) if un else "")

    # ------------------------------------------------------------------ プレースホルダー
    def _fill_placeholders(self):
        keys = []
        for r in self.results:
            for fr in r.fields.values():
                for ex in [fr.rep] + list(fr.others):
                    if ex is not None:
                        keys += [k for k in keys_in(ex.template) if k not in keys]
        keys += [k for k in self.state.project.values if k not in keys]
        while self.ph_layout.count():
            w = self.ph_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.ph_edits.clear()
        for k in keys:
            self.ph_layout.addWidget(QLabel("{{" + k + "}}"))
            e = QLineEdit(self.state.project.values.get(k, ""))
            e.setMaximumWidth(150)
            e.setPlaceholderText("今回の値")
            e.editingFinished.connect(lambda key=k, w=e: self._placeholder_changed(key, w.text()))
            self.ph_edits[k] = e
            self.ph_layout.addWidget(e)
        self.ph_layout.addStretch(1)

    def _placeholder_changed(self, key: str, value: str):
        vals = self.state.project.values
        if vals.get(key, "") == value.strip():
            return
        if value.strip():
            vals[key] = value.strip()
        else:
            vals.pop(key, None)
        self.state.save_project()
        for c in self.cards.values():
            c.refresh()
        self.notify("今回の案件の値を差し替えました")

    # ------------------------------------------------------------------ 編集の保存
    def save_edit(self, item_key, field, ja, en):
        self.edits[(item_key, field)] = (ja, en)
        self.state.db.save_edit(self.state.project.id, item_key, field, ja, en)

    def drop_edit(self, item_key, field):
        self.edits.pop((item_key, field), None)
        self.state.db.delete_edit(self.state.project.id, item_key, field)

    # ------------------------------------------------------------------ コピー
    def _layout(self):
        f = self.advisor.format
        mode = self.copy_mode.currentData()
        if f and f["profile"].get("columns"):
            return copytext.layout(f["profile"], f.get("analysis"), mode)
        return [(str(i), m) for i, m in enumerate(["wbs_no", "phase", "name"] + self.advisor.field_list())]

    def _row_values(self, r: ItemResult, lang: str) -> dict[str, str]:
        values = self.state.project.values
        en = lang == "en"
        out = {"wbs_no": r.wbs_no, "phase": (self.advisor.translate(r.phase)[0] if en else r.phase),
               "name": (r.name_en if en else r.name)}
        if r is self.current and self.cards:
            for f, card in self.cards.items():
                ja_t, en_t = card.texts()
                out[f] = en_t if en else ja_t
            return out
        for f, fr in r.fields.items():
            edit = self.edits.get((r.key, f))
            if edit:
                ja = substitute(edit[0], values)
                en_t = substitute(edit[1] or self.advisor.translate(edit[0])[0], self.advisor.values_en(values))
            else:
                ja, en_t, _ = self.advisor.render(fr.rep, values)
            allowed = fr.constraint.get("allowed")
            if allowed and ja:
                conv, _n = self.advisor.convert_allowed(ja, allowed)
                if conv != ja:
                    ja, en_t = conv, self.advisor.translate(conv)[0]
            out[f] = en_t if en else ja
        return out

    def copy_row(self, lang: str):
        if not self.current:
            return
        cols = self._layout()
        vals = self._row_values(self.current, lang)
        QGuiApplication.clipboard().setText(copytext.row_text(vals, cols))
        n = sum(1 for _c, m in cols if m and vals.get(m))
        self.notify(f"コピーしました（{'日本語' if lang == 'ja' else '英語'}・{n}欄）")

    def copy_all(self, lang: str):
        if not self.results:
            return
        cols = self._layout()
        rows = [self._row_values(r, lang) for r in self.results]
        QGuiApplication.clipboard().setText(copytext.rows_text(rows, cols))
        n = sum(1 for v in rows for _c, m in cols if m and v.get(m))
        self.notify(f"コピーしました（{'日本語' if lang == 'ja' else '英語'}・{len(rows)}行・{n}欄）")

    def copy_current(self, lang: str):
        """Ctrl+J／Ctrl+E：入力中の欄があればその欄、なければ選択中の作業項目の 1 行分。"""
        card = next((c for c in self.cards.values() if c.ja.hasFocus() or c.en.hasFocus()), None)
        if card:
            card.copy(lang)
        else:
            self.copy_row(lang)

    def focus_search(self):
        self.topic.setFocus()
        self.topic.selectAll()
        if self.topic.text().strip():
            self.search()

    # ------------------------------------------------------------------ 記載先のプレビュー
    def _load_preview(self):
        f = self.state.db.active_format("format")
        self.advisor.format = f
        if not f:
            self.grid.clear()
            self.grid.setRowCount(0)
            self.target_label.setText("「3. フォーマット・ひな形登録」でフォーマットを登録すると、記載先を表示します。")
            return
        try:
            self.grid.load(f["file"], f["profile"]["sheet"])
            self.grid.highlight(f["profile"])
        except Exception:
            self.target_label.setText("フォーマットのプレビューを表示できませんでした。")
        self._highlight()

    def _highlight(self):
        f = self.state.db.active_format("format")
        if not f or not self.current:
            return
        field = self.current_field or next(iter(self.current.fields), None)
        if not field:
            return
        self.grid.highlight(f["profile"])
        cells, status = inspect.target_cells(f["analysis"], f["profile"], self.current.key, self.current.wbs_no,
                                             self.current.name, field)
        self.grid.show_targets(cells)
        label = FIELDS.get(field, field)
        if status in ("ok", "picked"):
            self.target_label.setText(f"記載先：{cells[0]}（{self.current.name}・{label}）" + ("　※指定済み" if status == "picked" else ""))
            self.target_label.setStyleSheet("")
        else:
            why = "該当する行が複数あります" if status == "ambiguous" else "フォーマットに該当する行が見つかりません"
            self.target_label.setText(f"記載先を判断できません（{why}）。プレビューで記載先のセルをクリックし、"
                                      f"［このセルを記載先にする］を押してください。（{self.current.name}・{label}）")
            self.target_label.setStyleSheet("color:#b35c00")

    def _cell_picked(self, row: int, col: str):
        self.picked = f"{col}{row}"
        self.pick_label.setText(f"選択：{self.picked}")

    @guarded("記載先を指定できませんでした")
    def apply_pick(self):
        if not self.picked or not self.current or not self.state.db.active_format("format"):
            return
        field = self.current_field or next(iter(self.current.fields), None)
        workflow.set_target(self.state.db, self.current.key, field, self.picked)
        self.advisor.format = self.state.db.active_format("format")
        self.pick_label.setText(f"{self.picked} を記載先にしました（プロファイルに保存）")
        self._highlight()

    # ------------------------------------------------------------------ 転記チェックリスト
    def _fill_checklist(self):
        done = self.state.db.checklist(self.state.project.id)
        entries = []
        for r in self.results:
            for f, fr in r.fields.items():
                if fr.rep is not None or (r.key, f) in self.edits:
                    entries.append((r.key, f, (f"{r.wbs_no} " if r.wbs_no else "") + r.name))
        known = {(k, f) for k, f, _ in entries}
        names = {i.key: (f"{i.wbs_no} " if i.wbs_no else "") + i.name for i in self.advisor.items.values()} \
            if self.advisor.ready else {}
        for (k, f) in done:
            if (k, f) not in known:
                entries.append((k, f, names.get(k, k.split(":", 1)[-1])))
        self.check.blockSignals(True)
        self.check.clear()
        for k, f, name in entries:
            it = QTreeWidgetItem([name, FIELDS.get(f, f), ""])
            it.setData(0, Qt.UserRole, (k, f))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(2, Qt.Checked if done.get((k, f)) else Qt.Unchecked)
            self.check.addTopLevelItem(it)
        self.check.blockSignals(False)
        self._update_check_label()

    def _check_changed(self, it, col):
        if col != 2:
            return
        k, f = it.data(0, Qt.UserRole)
        self.state.db.set_check(self.state.project.id, k, f, it.checkState(2) == Qt.Checked)
        self._update_check_label()

    def _update_check_label(self):
        n = self.check.topLevelItemCount()
        d = sum(1 for i in range(n) if self.check.topLevelItem(i).checkState(2) == Qt.Checked)
        self.check_label.setText(f"案件「{self.state.project.name}」：転記済み {d}／{n}（次回起動時も続きから再開できます）")
