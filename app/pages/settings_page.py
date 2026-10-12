"""6. 設定：閾値（判定・照合・検索）、用語辞書、定型文テンプレート、プレースホルダー。"""
from __future__ import annotations

import copy
import csv
import json
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from core import config as config_mod, paths
from core.models import FIELDS

from ..widgets import AppState, guarded, ro_item

REASON_KEYS = {"reason_stat": "必要性の根拠（採用あり）", "reason_none": "必要性の根拠（採用なし）",
               "reason_no_cases": "過去事例なし", "reason_rule": "ルールに一致", "reason_prefer": "優先ルールを適用",
               "reason_extra": "ひな形外の項目"}


def _pct_spin(value: float) -> QSpinBox:
    s = QSpinBox()
    s.setRange(0, 100)
    s.setSuffix(" %")
    s.setValue(int(round(value * 100)))
    return s


def _dspin(value: float, lo=0.0, hi=5.0, step=0.05) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(2)
    s.setValue(float(value))
    return s


class SettingsPage(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        tabs = QTabWidget()
        tabs.addTab(self._general(), "閾値・検索")
        tabs.addTab(self._glossary(), "用語辞書")
        tabs.addTab(self._templates(), "定型文テンプレート")
        tabs.addTab(self._placeholders(), "プレースホルダー")
        self.tabs = tabs
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        info = QLabel(f"データ・設定・ログの保存先：{paths.home()}　（Excel ファイルは読むだけで、変更しません）")
        info.setStyleSheet("color:#666")
        layout.addWidget(info)

    # ---- 閾値・検索 ----
    def _general(self):
        c = self.state.cfg
        w = QWidget()
        form = QFormLayout(w)
        self.required = _pct_spin(c["thresholds"]["required"])
        self.unneeded = _pct_spin(c["thresholds"]["unneeded"])
        form.addRow("「必要」とする採用率（以上）", self.required)
        form.addRow("「不要」とする採用率（未満）", self.unneeded)
        self.auto = QSpinBox()
        self.auto.setRange(0, 100)
        self.auto.setValue(int(c["match"]["auto"]))
        self.review = QSpinBox()
        self.review.setRange(0, 100)
        self.review.setValue(int(c["match"]["review"]))
        form.addRow("照合：自動で対応付ける類似度（以上）", self.auto)
        form.addRow("照合：照合確認に出す類似度（以上）", self.review)
        s = c["search"]
        self.w_bm25 = _dspin(s["bm25"])
        self.w_name = _dspin(s["name"])
        self.min_score = _dspin(s["min_score"], 0, 2, 0.01)
        self.top_n = QSpinBox()
        self.top_n.setRange(1, 500)
        self.top_n.setValue(int(s["top_n"]))
        form.addRow("検索：全文検索（BM25）の重み", self.w_bm25)
        form.addRow("検索：作業項目名の類似度の重み", self.w_name)
        form.addRow("検索：表示する最低点", self.min_score)
        form.addRow("検索：表示する作業項目の数（最大）", self.top_n)
        r = c["representative"]
        self.w_freq = _dspin(r["freq"])
        self.w_central = _dspin(r["central"])
        self.n_others = QSpinBox()
        self.n_others.setRange(0, 20)
        self.n_others.setValue(int(r["others"]))
        form.addRow("推奨記載例：出現頻度の重み", self.w_freq)
        form.addRow("推奨記載例：他の記載との類似度の重み", self.w_central)
        form.addRow("他の記載例の件数", self.n_others)
        wt = c["weighting"]
        self.coef = _dspin(wt.get("coef", 1.0), 0, 10, 0.1)
        form.addRow("属性で重み付け：重みの強さ（重み = 1 + 強さ × 属性の類似度）", self.coef)
        self.filter_min = _pct_spin(s.get("filter_min_similarity", 0.5))
        form.addRow("属性で絞り込み：属性の類似度（以上）", self.filter_min)
        self.bands = QLineEdit(", ".join(str(b) for b in c.get("bed_bands", [])))
        form.addRow("病床規模の区分の境目（床）", self.bands)
        self.update_types = QLineEdit("、".join(c["attributes"]["update_types"]))
        self.systems = QLineEdit("、".join(c["attributes"]["systems"]))
        form.addRow("更新種別の選択肢", self.update_types)
        form.addRow("連携システムの選択肢", self.systems)
        fl = c["fields"]
        self.text_fields = QLineEdit("、".join(FIELDS.get(f, f) for f in fl["text"]))
        self.value_fields = QLineEdit("、".join(FIELDS.get(f, f) for f in fl["value"]))
        form.addRow("候補を出す欄（文章）", self.text_fields)
        form.addRow("候補を出す欄（担当・日付など）", self.value_fields)
        save = QPushButton("設定を保存")
        save.setStyleSheet("font-weight:bold")
        save.clicked.connect(lambda: self.save_general())
        form.addRow("", save)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(w)
        return scroll

    @guarded("設定を保存できませんでした")
    def save_general(self):
        c = self.state.cfg
        if self.unneeded.value() > self.required.value():
            raise ValueError("「不要」の採用率は「必要」の採用率以下にしてください。")
        if self.review.value() > self.auto.value():
            raise ValueError("照合確認に出す類似度は、自動で対応付ける類似度以下にしてください。")
        split = lambda s: [x.strip() for x in s.replace(",", "、").split("、") if x.strip()]
        by_label = {v: k for k, v in FIELDS.items()}
        fields = {}
        for key, edit in (("text", self.text_fields), ("value", self.value_fields)):
            names = split(edit.text())
            bad = [n for n in names if n not in by_label]
            if bad:
                raise ValueError(f"欄の名前が正しくありません：{'、'.join(bad)}（使える名前：{'、'.join(FIELDS.values())}）")
            fields[key] = [by_label[n] for n in names]
        c["thresholds"] = {"required": self.required.value() / 100, "unneeded": self.unneeded.value() / 100}
        c["match"].update(auto=self.auto.value(), review=self.review.value())
        c["search"].update(bm25=self.w_bm25.value(), name=self.w_name.value(), min_score=self.min_score.value(),
                           top_n=self.top_n.value(), filter_min_similarity=self.filter_min.value() / 100)
        c["representative"].update(freq=self.w_freq.value(), central=self.w_central.value(), others=self.n_others.value())
        c["weighting"]["coef"] = self.coef.value()
        c["bed_bands"] = [int(x) for x in self.bands.text().replace("、", ",").split(",") if x.strip().isdigit()]
        c["attributes"]["update_types"] = split(self.update_types.text())
        c["attributes"]["systems"] = split(self.systems.text())
        c["fields"] = fields
        self.state.save_config()
        QMessageBox.information(self, "設定", "設定を保存しました。次の検索から反映されます（属性の選択肢は再起動後に反映されます）。")

    # ---- 用語辞書 ----
    def _glossary(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(QLabel("項目名などの英訳に使います。英語が空欄の語は、英文に【未翻訳: ○○】と表示されます。"))
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
        self.load_glossary()
        return w

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
        QMessageBox.information(self, "用語辞書", f"保存しました（{len(terms)}語）。次の検索から英文に反映されます。")
        self.state.settingsChanged.emit()

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

    # ---- 定型文テンプレート ----
    def _templates(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(QLabel("報告内容の日英対訳テンプレートです。{x} の部分は用語辞書で訳して差し込みます（上にあるものほど優先）。"
                           "過去事例の英語の列・自分の定型文の英文がある文は、そちらを優先します。"))
        self.ph = QTableWidget(0, 2)
        self.ph.setHorizontalHeaderLabels(["日本語（{x} を含められます）", "英語"])
        self.ph.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for p in self.state.templates.get("phrases", []):
            self._ph_row(p.get("ja", ""), p.get("en", ""))
        b = QHBoxLayout()
        add = QPushButton("行を追加")
        add.clicked.connect(lambda: self._ph_row("", ""))
        rm = QPushButton("選んだ行を削除")
        rm.clicked.connect(lambda: [self.ph.removeRow(r) for r in sorted({i.row() for i in self.ph.selectedIndexes()}, reverse=True)])
        b.addWidget(add)
        b.addWidget(rm)
        b.addStretch(1)
        l.addWidget(self.ph, 2)
        l.addLayout(b)
        l.addWidget(QLabel("必要性の根拠の文：{total}（事例数）{adopted}（採用数）{rate}（採用率%）{rule}（ルール名）が使えます。"))
        self.tt = QTableWidget(0, 3)
        self.tt.setHorizontalHeaderLabels(["種類", "日本語", "英語"])
        self.tt.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tt.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        for key, label in REASON_KEYS.items():
            v = self.state.templates.get(key, {})
            i = self.tt.rowCount()
            self.tt.insertRow(i)
            self.tt.setItem(i, 0, ro_item(label, key))
            self.tt.setItem(i, 1, QTableWidgetItem(v.get("ja", "")))
            self.tt.setItem(i, 2, QTableWidgetItem(v.get("en", "")))
        l.addWidget(self.tt, 1)
        l.addWidget(QLabel("自分の定型文（検索画面の［定型文として保存］で登録したもの）"))
        self.my = QTableWidget(0, 4)
        self.my.setHorizontalHeaderLabels(["作業項目", "欄", "日本語", "英語"])
        self.my.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.my.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        l.addWidget(self.my, 1)
        b2 = QHBoxLayout()
        reload_ = QPushButton("一覧を更新")
        reload_.clicked.connect(lambda: self._load_my())
        del_my = QPushButton("選んだ定型文を削除")
        del_my.clicked.connect(lambda: self.delete_my())
        b2.addWidget(reload_)
        b2.addWidget(del_my)
        b2.addStretch(1)
        save = QPushButton("テンプレートを保存")
        save.setStyleSheet("font-weight:bold")
        save.clicked.connect(lambda: self.save_templates())
        b2.addWidget(save)
        l.addLayout(b2)
        self._load_my()
        return w

    def _ph_row(self, ja, en):
        i = self.ph.rowCount()
        self.ph.insertRow(i)
        self.ph.setItem(i, 0, QTableWidgetItem(ja))
        self.ph.setItem(i, 1, QTableWidgetItem(en))

    def _load_my(self):
        names = {i.key: i.name for i in self.state.db.master_items()}
        self.my.setRowCount(0)
        for p in self.state.db.phrases():
            i = self.my.rowCount()
            self.my.insertRow(i)
            for col, v in enumerate((names.get(p["item_key"], p["item_key"].split(":", 1)[-1]),
                                     FIELDS.get(p["field"], p["field"]), p["ja"], p["en"] or "")):
                self.my.setItem(i, col, ro_item(v, p["id"]))

    @guarded("削除できませんでした")
    def delete_my(self):
        ids = {self.my.item(i.row(), 0).data(Qt.UserRole) for i in self.my.selectedIndexes()}
        for pid in ids:
            self.state.db.delete_phrase(pid)
        self._load_my()
        self.state.settingsChanged.emit()

    @guarded("テンプレートを保存できませんでした")
    def save_templates(self):
        t = copy.deepcopy(self.state.templates)
        phrases = []
        for i in range(self.ph.rowCount()):
            ja = (self.ph.item(i, 0) or QTableWidgetItem("")).text().strip()
            en = (self.ph.item(i, 1) or QTableWidgetItem("")).text().strip()
            if ja and en:
                if ja.count("{x}") > 1:
                    raise ValueError(f"{{x}} は 1 つの文に 1 か所だけ使えます：{ja}")
                phrases.append({"ja": ja, "en": en})
        t["phrases"] = phrases
        for i in range(self.tt.rowCount()):
            key = self.tt.item(i, 0).data(Qt.UserRole)
            t[key] = {"ja": self.tt.item(i, 1).text(), "en": self.tt.item(i, 2).text()}
        config_mod.save_text_templates(t)
        self.state.templates.clear()
        self.state.templates.update(config_mod.load_text_templates())
        self.state.settingsChanged.emit()
        QMessageBox.information(self, "定型文テンプレート", "保存しました。次の検索から反映されます。")

    # ---- プレースホルダー ----
    def _placeholders(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(QLabel("推奨記載例の中で、今回の案件の値に差し替える語の見つけ方です（上にあるものほど優先）。"
                           "「正規表現」か「取得元」（vendors＝過去事例のベンダー名、person＝人名）のどちらかを指定します。"))
        self.pt = QTableWidget(0, 5)
        self.pt.setHorizontalHeaderLabels(["使う", "名前", "英語名", "正規表現", "取得元"])
        self.pt.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        for p in self.state.cfg.get("placeholders", []):
            self._pt_row(p)
        b = QHBoxLayout()
        add = QPushButton("行を追加")
        add.clicked.connect(lambda: self._pt_row({"name": "", "en": "", "enabled": True}))
        rm = QPushButton("選んだ行を削除")
        rm.clicked.connect(lambda: [self.pt.removeRow(r) for r in sorted({i.row() for i in self.pt.selectedIndexes()}, reverse=True)])
        up = QPushButton("↑")
        up.clicked.connect(lambda: self._pt_move(-1))
        down = QPushButton("↓")
        down.clicked.connect(lambda: self._pt_move(1))
        for x in (add, rm, up, down):
            b.addWidget(x)
        b.addStretch(1)
        save = QPushButton("プレースホルダーを保存")
        save.setStyleSheet("font-weight:bold")
        save.clicked.connect(lambda: self.save_placeholders())
        b.addWidget(save)
        l.addWidget(self.pt, 1)
        l.addLayout(b)
        return w

    def _pt_row(self, p, at=None):
        i = self.pt.rowCount() if at is None else at
        self.pt.insertRow(i)
        cb = QTableWidgetItem("")
        cb.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        cb.setCheckState(Qt.Checked if p.get("enabled", True) else Qt.Unchecked)
        self.pt.setItem(i, 0, cb)
        for col, key in ((1, "name"), (2, "en"), (3, "pattern"), (4, "source")):
            self.pt.setItem(i, col, QTableWidgetItem(p.get(key, "")))

    def _pt_values(self, i) -> dict:
        get = lambda c: (self.pt.item(i, c) or QTableWidgetItem("")).text().strip()
        d = {"name": get(1), "en": get(2), "enabled": self.pt.item(i, 0).checkState() == Qt.Checked}
        if get(3):
            d["pattern"] = get(3)
        if get(4):
            d["source"] = get(4)
        return d

    def _pt_move(self, d):
        rows = sorted({i.row() for i in self.pt.selectedIndexes()})
        if len(rows) != 1 or not 0 <= rows[0] + d < self.pt.rowCount():
            return
        r = rows[0]
        v = self._pt_values(r)
        self.pt.removeRow(r)
        self._pt_row(v, r + d)
        self.pt.selectRow(r + d)

    @guarded("プレースホルダーを保存できませんでした")
    def save_placeholders(self):
        out = []
        for i in range(self.pt.rowCount()):
            p = self._pt_values(i)
            if not p["name"]:
                continue
            if p.get("pattern"):
                try:
                    re.compile(p["pattern"])
                except re.error as e:
                    raise ValueError(f"「{p['name']}」の正規表現が正しくありません（{e}）。")
            elif p.get("source") not in ("vendors", "person"):
                raise ValueError(f"「{p['name']}」は正規表現か取得元（vendors / person）を指定してください。")
            out.append(p)
        self.state.cfg["placeholders"] = out
        self.state.save_config()
        QMessageBox.information(self, "プレースホルダー", "保存しました。次の検索から反映されます。")
