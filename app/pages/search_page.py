"""記載例を探す（メイン画面）。

上部：トピック（作業項目名を補完）と、{{日付}} などに入れる今回の値
左  ：見つかった作業項目
中央：欄ごとの日本語／英語の記載例（推奨・他の記載例との入れ替え・編集）とコピー
"""
from __future__ import annotations

import re

from PySide6.QtCore import QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QComboBox, QCompleter, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from core import copytext
from core.advisor import FieldResult, ItemResult
from core.models import FIELDS
from core.placeholders import keys_in, substitute

from ..widgets import AppState, guarded


class FieldCard(QFrame):
    """1 つの欄：日本語／英語の文、コピー、他の記載例との入れ替え、編集。"""
    focused = Signal(str)

    def __init__(self, page: "SearchPage", item: ItemResult, fr: FieldResult, parent=None):
        super().__init__(parent)
        self.page, self.item, self.fr = page, item, fr
        self.field = fr.field
        self.current = fr.rep
        self.edited: tuple[str, str] | None = None        # 編集した文（日本語, 英語。英語が空なら自動で訳す）
        self._loading = False
        self.setFrameShape(QFrame.StyledPanel)
        g = QGridLayout(self)
        g.addWidget(QLabel(f"<b>{FIELDS.get(self.field, self.field)}</b>"), 0, 0)
        self.info = QLabel("")
        self.info.setStyleSheet("color:#555")
        g.addWidget(self.info, 0, 1)
        self.ja, self.en = QPlainTextEdit(), QPlainTextEdit()
        for w in (self.ja, self.en):
            w.setMaximumHeight(80)
            w.installEventFilter(self)
        g.addWidget(self.ja, 1, 0)
        g.addWidget(self.en, 1, 1)
        rj, re_ = QHBoxLayout(), QHBoxLayout()
        bj, be = QPushButton("日本語コピー"), QPushButton("英語コピー")
        bj.clicked.connect(lambda: self.copy("ja"))
        be.clicked.connect(lambda: self.copy("en"))
        self.others = QComboBox()
        self.others.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.others.setMinimumContentsLength(8)
        self.others.setToolTip("他の記載例を選ぶと、表示中の文と入れ替えます")
        self.others.activated.connect(self._choose_other)
        reset = QPushButton("推奨に戻す")
        reset.clicked.connect(self.reset)
        rj.addWidget(bj)
        rj.addWidget(self.others, 1)
        re_.addWidget(be)
        re_.addStretch(1)
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
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._ja_settled)
        self.ja.textChanged.connect(lambda: None if self._loading else self._timer.start())
        self.en.textChanged.connect(self._en_changed)
        self._fill_others()
        self.refresh()

    def _fill_others(self):
        self.others.clear()
        n = len(self.fr.others)
        self.others.addItem(f"他の記載例（{n}件）" if n else "他の記載例なし")
        for ex in self.fr.others:
            text = ex.template.replace("\n", " ")
            self.others.addItem(f"{text[:60]}{'…' if len(text) > 60 else ''}　［{ex.label()}］")

    def refresh(self):
        """今回の値（プレースホルダー）・編集を反映して表示し直す。"""
        values = self.page.state.values
        adv = self.page.advisor
        self._loading = True
        if self.edited:
            ja_t, en_t = self.edited
            self.ja.setPlainText(substitute(ja_t, values))
            self.en.setPlainText(substitute(en_t or adv.translate(ja_t)[0], adv.values_en(values)))
        else:
            ja, en, _ = adv.render(self.current, values)
            self.ja.setPlainText(ja)
            self.en.setPlainText(en)
        self._loading = False
        self._update_info()

    def _update_info(self):
        text = self.ja.toPlainText()
        self.info.setText(f"文字数の目安：{self.fr.limit}字" if self.fr.limit else "")
        warns = []
        if self.fr.limit and len(text) > self.fr.limit:
            warns.append(f"文字数が目安を超えています（{len(text)}字）")
        un = re.findall(r"【未翻訳: ([^】]*)】", self.en.toPlainText())
        if un:
            warns.append("未翻訳の語：" + "、".join(un) + "（「用語辞書」で英語を登録できます）")
        left = keys_in(text)
        if left:
            warns.append("今回の値を入れていない箇所：" + " ".join("{{" + k + "}}" for k in left))
        self.warn.setText("\n".join(warns))
        ex = self.current
        self.source.setText("参考にした過去事例：" + (ex.label() + (f"（{ex.count}件で同じ記載）" if ex.count > 1 else "")
                                              if ex else "この欄の記載はありません"))

    def texts(self) -> tuple[str, str]:
        return self.ja.toPlainText().strip(), self.en.toPlainText().strip()

    def copy(self, lang: str):
        ja, en = self.texts()
        QGuiApplication.clipboard().setText(ja if lang == "ja" else en)
        self.page.notify(f"コピーしました（{'日本語' if lang == 'ja' else '英語'}・1欄）")

    def eventFilter(self, obj, ev):
        if ev.type() == ev.Type.FocusIn:
            self.page.current_field = self.field
        return False

    def _ja_settled(self):
        ja = self.ja.toPlainText()
        en_manual = bool(self.edited and self.edited[1])
        if not en_manual:
            self._loading = True
            self.en.setPlainText(self.page.advisor.translate(ja)[0])
            self._loading = False
        self.edited = (ja, self.en.toPlainText() if en_manual else "")
        self._update_info()

    def _en_changed(self):
        if self._loading:
            return
        self.edited = (self.ja.toPlainText(), self.en.toPlainText())
        self._update_info()

    def _choose_other(self, index: int):
        if index <= 0:
            return
        ex = self.fr.others[index - 1]
        if self.current is not None:
            self.fr.others[index - 1] = self.current
        else:
            self.fr.others.pop(index - 1)
        self.current, self.edited = ex, None
        self._fill_others()
        self.refresh()

    def reset(self):
        self.current, self.edited = self.fr.rep, None
        self.refresh()


class SearchPage(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.results: list[ItemResult] = []
        self.cards: dict[str, FieldCard] = {}
        self.current: ItemResult | None = None
        self.current_field: str | None = None

        self.topic = QLineEdit()
        self.topic.setPlaceholderText("② トピックを入力して Enter（例：マスタ移行、連携テスト、診療報酬改定対応）")
        self.topic.setStyleSheet("font-size:15px; padding:4px")
        self.topic.returnPressed.connect(lambda: self.search())
        self.completer = QCompleter([], self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.topic.setCompleter(self.completer)
        b_search = QPushButton("検索（Ctrl+F）")
        b_search.clicked.connect(lambda: self.search())
        row1 = QHBoxLayout()
        row1.addWidget(self.topic, 1)
        row1.addWidget(b_search)

        self.ph_box = QWidget()
        self.ph_layout = QHBoxLayout(self.ph_box)
        self.ph_layout.setContentsMargins(0, 0, 0, 0)
        self.ph_edits: dict[str, QLineEdit] = {}
        self.ph_title = QLabel("今回の値")
        row2 = QHBoxLayout()
        row2.addWidget(self.ph_title)
        row2.addWidget(self.ph_box, 1)

        self.items = QTreeWidget()
        self.items.setHeaderLabels(["③ 作業項目", "記載のあった事例"])
        self.items.setRootIsDecorated(False)
        self.items.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.items.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.items.currentItemChanged.connect(lambda cur, _p: self._item_selected(cur))

        self.head = QLabel("① 「過去事例」タブで過去事例を取り込み、② トピックを入力してください。")
        self.head.setWordWrap(True)
        self.head.setTextInteractionFlags(Qt.TextSelectableByMouse)
        b_row_ja, b_row_en = QPushButton("⑤ 1行コピー（日本語）"), QPushButton("⑤ 1行コピー（英語）")
        b_row_ja.setToolTip("過去事例と同じ列順のタブ区切り。Excel の行頭のセルに貼ると 1 行分が埋まります")
        b_row_ja.clicked.connect(lambda: self.copy_row("ja"))
        b_row_en.clicked.connect(lambda: self.copy_row("en"))
        hb = QHBoxLayout()
        hb.addWidget(QLabel("<b>④ 記載例（日本語）</b>"), 1)
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
        cl.addLayout(hb)
        cl.addWidget(scroll, 1)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.items)
        split.addWidget(center)
        split.setSizes([380, 1100])
        layout = QVBoxLayout(self)
        layout.addLayout(row1)
        layout.addLayout(row2)
        layout.addWidget(split, 1)
        state.indexReady.connect(self._index_ready)
        self._fill_placeholders([])

    @property
    def advisor(self):
        return self.state.advisor

    def notify(self, text: str):
        win = self.window()
        if hasattr(win, "statusBar"):
            win.statusBar().showMessage(text, 5000)

    # ------------------------------------------------------------------ 検索
    def _index_ready(self):
        self.completer.setModel(QStringListModel(self.advisor.item_names(), self.completer))
        if self.topic.text().strip():
            self.search()
        elif not self.advisor.cases:
            self.head.setText("① 「過去事例」タブで過去事例（記載済みの実用WBS）を取り込んでください。")
        else:
            self.head.setText(f"過去事例 {len(self.advisor.cases)}件を読み込みました。② トピックを入力してください。")

    @guarded("検索できませんでした")
    def search(self):
        if not self.advisor.ready:
            self.head.setText("検索の準備をしています。しばらくお待ちください。")
            return
        topic = self.topic.text().strip()
        if not topic:
            return
        self.results = self.advisor.search(topic)
        self.items.blockSignals(True)
        self.items.clear()
        for r in self.results:
            it = QTreeWidgetItem([(f"{r.wbs_no} " if r.wbs_no else "") + r.name, f"{len(r.cases)}／{r.total}件"])
            it.setData(0, Qt.UserRole, r.key)
            it.setToolTip(0, "、".join(r.cases))
            self.items.addTopLevelItem(it)
        self.items.blockSignals(False)
        self._fill_placeholders(self.advisor.placeholder_keys(self.results))
        if self.results:
            self.items.setCurrentItem(self.items.topLevelItem(0))
        else:
            self.current = None
            self._clear_cards()
            self.head.setText(f"「{topic}」に関連する記載は見つかりませんでした。別の言葉で試してください。")
        self.notify(f"{len(self.results)}件の作業項目が見つかりました")

    def _item_selected(self, it):
        key = it.data(0, Qt.UserRole) if it else None
        self.current = next((r for r in self.results if r.key == key), None)
        self._clear_cards()
        r = self.current
        if r is None:
            return
        self.head.setText(f"<b>{(r.wbs_no + ' ') if r.wbs_no else ''}{r.name}</b>　{r.phase}　／　{r.name_en}<br>"
                          f"参考にした過去事例：{'、'.join(r.cases)}")
        for f, fr in r.fields.items():
            if fr.rep is None:
                continue
            card = FieldCard(self, r, fr)
            self.cards[f] = card
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        self.current_field = next(iter(self.cards), None)

    def _clear_cards(self):
        for c in self.cards.values():
            c.setParent(None)
            c.deleteLater()
        self.cards.clear()

    # ------------------------------------------------------------------ 今回の値（プレースホルダー）
    def _fill_placeholders(self, keys: list[str]):
        while self.ph_layout.count():
            w = self.ph_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.ph_edits.clear()
        self.ph_title.setVisible(bool(keys))
        for k in keys:
            self.ph_layout.addWidget(QLabel("{{" + k + "}}"))
            e = QLineEdit(self.state.values.get(k, ""))
            e.setMaximumWidth(150)
            e.setPlaceholderText("今回の値")
            e.editingFinished.connect(lambda key=k, w=e: self.set_value(key, w.text()))
            self.ph_edits[k] = e
            self.ph_layout.addWidget(e)
        self.ph_layout.addStretch(1)

    def set_value(self, key: str, value: str):
        value = value.strip()
        if self.state.values.get(key, "") == value:
            return
        if value:
            self.state.values[key] = value
        else:
            self.state.values.pop(key, None)
        self.state.save_values()
        if key in self.ph_edits and self.ph_edits[key].text() != value:
            self.ph_edits[key].setText(value)
        for c in self.cards.values():
            c.refresh()
        self.notify("今回の値に差し替えました")

    # ------------------------------------------------------------------ コピー
    def copy_row(self, lang: str):
        if not self.current:
            return
        r = self.current
        en = lang == "en"
        values = {"wbs_no": r.wbs_no, "phase": self.advisor.translate(r.phase)[0] if en else r.phase,
                  "name": r.name_en if en else r.name}
        for f, card in self.cards.items():
            ja_t, en_t = card.texts()
            values[f] = en_t if en else ja_t
        cols = copytext.layout(self.advisor.columns) or [(str(i), m) for i, m in
                                                          enumerate(["wbs_no", "phase", "name"] + list(self.cards))]
        QGuiApplication.clipboard().setText(copytext.row_text(values, cols))
        n = sum(1 for _c, m in cols if m and values.get(m))
        self.notify(f"コピーしました（{'日本語' if lang == 'ja' else '英語'}・{n}欄）")

    def copy_current(self, lang: str):
        """Ctrl+J／Ctrl+E：入力中の欄があればその欄、なければ 1 行分。"""
        card = next((c for c in self.cards.values() if c.ja.hasFocus() or c.en.hasFocus()), None)
        if card:
            card.copy(lang)
        else:
            self.copy_row(lang)

    def focus_search(self):
        self.topic.setFocus()
        self.topic.selectAll()
