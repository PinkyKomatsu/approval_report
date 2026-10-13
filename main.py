"""WBS 記載例アシスタント（起動）。

    ApprovalReport.exe                  通常起動
    ApprovalReport.exe --selftest DIR   画面を自動で操作し、取込 → 検索 → 作業項目の選択 → 値の差し替え → コピー を行って
                                        結果（selftest.json と画面の画像）を DIR に保存する（データは DIR の中に作る）
                                        --fixtures DIR でテスト用の Excel の場所を指定する
"""
from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging():
    from core import paths
    h = RotatingFileHandler(paths.logs_dir() / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log = logging.getLogger("approvalreport")
    log.setLevel(logging.INFO)
    log.addHandler(h)
    return log


def install_excepthook(log):
    """捕捉されなかった例外：ログには種類と場所だけを書き（セルの内容を含み得るメッセージは書かない）、日本語で知らせる。"""
    def hook(etype, value, tb):
        where = " <- ".join(f"{Path(f.filename).name}:{f.lineno}" for f in reversed(traceback.extract_tb(tb)[-4:]))
        log.error("未処理の例外 %s at %s", etype.__name__, where)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            from app.widgets import error_message
            if QApplication.instance():
                QMessageBox.critical(None, "エラー", error_message(value))
        except Exception:
            pass
    sys.excepthook = hook


def selftest(out: Path, fixtures: Path) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QMessageBox
    out.mkdir(parents=True, exist_ok=True)
    os.environ["APPROVALREPORT_HOME"] = str(out / "home")
    setup_logging()
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.No)
    app = QApplication(sys.argv)
    from app.main_window import MainWindow
    from excel_io import reader
    result = {"ok": False, "steps": {}}
    hashes = {p.name: reader.sha256(p) for p in fixtures.glob("*.xlsx")}
    win = MainWindow()
    win.show()

    def pump():
        for _ in range(20):
            app.processEvents()

    def shot(name):
        pump()
        win.grab().save(str(out / name))

    try:
        sp = win.search_page
        # ① 過去事例を取り込む（ひな形は記載がないので取り込まない）
        win.tabs.setCurrentWidget(win.cases_page)
        win.cases_page.import_files([fixtures / f"case_{x}.xlsx" for x in "ABC"] + [fixtures / "master.xlsx"], sync=True)
        result["steps"]["cases"] = len(win.state.db.cases())
        result["steps"]["log"] = win.cases_page.log.toPlainText().splitlines()
        shot("1_cases.png")
        win.rebuild_index(sync=True)
        while (win._worker is not None and win._worker.isRunning()) or win._pending:
            app.processEvents()
        pump()
        # ② トピックを入力 → ③ 作業項目を選ぶ
        sp.topic.setText("マスタ移行")
        sp.search()
        result["steps"]["items"] = [r.name for r in sp.results[:4]]
        i = next(i for i in range(sp.items.topLevelItemCount()) if "移行リハーサル" in sp.items.topLevelItem(i).text(0))
        sp.items.setCurrentItem(sp.items.topLevelItem(i))
        # ④ 今回の値を入れる
        sp.set_value("日付", "2026/11/20")
        card = sp.cards["report"]
        result["steps"]["report_ja"] = card.ja.toPlainText()
        result["steps"]["report_en"] = card.en.toPlainText()
        # ⑤ コピー
        sp.copy_row("ja")
        result["steps"]["row_ja"] = QGuiApplication.clipboard().text()
        sp.copy_row("en")
        result["steps"]["row_en"] = QGuiApplication.clipboard().text()
        win.tabs.setCurrentWidget(sp)
        shot("2_search.png")
        win.tabs.setCurrentWidget(win.glossary_page)
        shot("3_glossary.png")
        unchanged = {p.name: reader.sha256(p) for p in fixtures.glob("*.xlsx")} == hashes
        result["steps"]["excel_unchanged"] = unchanged
        result["ok"] = (result["steps"]["cases"] == 3 and "移行リハーサル" in result["steps"]["items"]
                        and "2026/11/20" in result["steps"]["report_ja"] and "2026/11/20" in result["steps"]["report_en"]
                        and result["steps"]["row_ja"].split("	")[5].startswith("移行リハーサルを2026/11/20") and unchanged)
    except Exception as e:
        result["error"] = f"{e}\n{traceback.format_exc()}"
    (out / "selftest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    QTimer.singleShot(0, app.quit)
    app.exec()
    return 0 if result["ok"] else 1


def main() -> int:
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        out = Path(sys.argv[i + 1])
        fx = Path(sys.argv[sys.argv.index("--fixtures") + 1]) if "--fixtures" in sys.argv else \
            Path(__file__).resolve().parent / "tests" / "fixtures"
        return selftest(out, fx)
    log = setup_logging()
    install_excepthook(log)
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    for family in ("Yu Gothic UI", "Meiryo UI"):
        if family in QFontDatabase.families():
            app.setFont(QFont(family, 10))
            break
    from app.main_window import MainWindow
    win = MainWindow()
    win.show()
    log.info("起動しました")
    win.rebuild_index()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
