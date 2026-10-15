# -*- coding: utf-8 -*-
"""程序入口。运行：venv\\Scripts\\python.exe main.py（可先执行 set_env.bat）。"""
from __future__ import annotations

import sys


def main():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("未安装 PySide6。请先运行 install_env.bat，或执行：")
        print("  venv\\Scripts\\python.exe -m pip install PySide6 "
              "-i https://mirrors.aliyun.com/pypi/simple/")
        return 1

    from view.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("MP4 字幕矫正工具")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
