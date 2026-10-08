"""工程承認会議 報告書 半自動作成ツール。"""
import sys

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from app.ui import MainWindow


def main():
    app = QApplication(sys.argv)
    for family in ("Yu Gothic UI", "Meiryo UI"):
        if family in QFontDatabase.families():
            app.setFont(QFont(family, 10))
            break
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
