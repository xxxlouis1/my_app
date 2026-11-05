# -*- coding: utf-8 -*-
"""全局暗色主题（第一板块 GUI）。Aboboo 式专业暗黑风格。"""

DARK_QSS = """
QWidget { background-color: #1a1a1a; color: #dbe2ee; font-size: 13px; }

QGroupBox { border: 1px solid #2a3346; border-radius: 6px; margin-top: 14px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #8b95a5; }

QLineEdit { background-color: #14141c; color: #dbe2ee; border: 1px solid #2a3346;
            border-radius: 4px; padding: 4px 6px; }
QLineEdit:read-only { color: #9fb2c8; }

QPushButton { background-color: #1e2a3a; color: #dbe2ee; border: 1px solid #2a3a50;
              border-radius: 4px; padding: 5px 12px; }
QPushButton:hover { background-color: #2a3a50; }
QPushButton:pressed { background-color: #16222f; }
QPushButton:disabled { background-color: #1a1a1a; color: #555555; border-color: #222222; }

QTableWidget { background-color: #1a1a1a; alternate-background-color: #1e1e1e; border: none; }
QHeaderView::section { background-color: #24242e; color: #c9d3e0; border: none;
                       border-bottom: 2px solid #2e2e3a; padding: 6px; font-weight: bold; }
QTableWidget::item:selected { background-color: #1e3a5f; color: #ffffff; }

QPlainTextEdit { background-color: #0f1220; color: #b8c2d0; border: 1px solid #2a3346;
                 border-radius: 4px; }

QProgressBar { background-color: #14141c; border: 1px solid #2a3346; border-radius: 4px;
               text-align: center; color: #dbe2ee; }
QProgressBar::chunk { background-color: #4fc3f7; border-radius: 3px; }

QTabWidget::pane { border: 1px solid #2a3346; }

QMenu { background-color: #1e1e2e; color: #dbe2ee; border: 1px solid #2a3346; }
QMenu::item:selected { background-color: #2a3a50; }
QMenu::item:disabled { color: #666666; }

QListWidget { background-color: #1e1e2e; color: #dbe2ee; border: none; }
QListWidget::item { padding: 10px 8px; border-radius: 4px; }
QListWidget::item:selected { background-color: #2a3a50; color: #ffffff; }
QListWidget::item:hover { background-color: #252533; }
QTabBar::tab { background: #1e1e2e; color: #8b95a5; padding: 6px 16px; border: none; }
QTabBar::tab:selected { background: #2a3a50; color: #dbe2ee; }
QTabBar::tab:hover { background: #252533; }

QSplitter::handle { background-color: #202020; }

QSlider::groove:horizontal { background: #333333; height: 4px; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #4fc3f7; border-radius: 2px; }
QSlider::handle:horizontal { background: #4fc3f7; width: 12px; margin: -4px 0; border-radius: 6px; }

QScrollBar:vertical { background: #14141c; width: 10px; }
QScrollBar::handle:vertical { background: #2a3346; border-radius: 5px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #14141c; height: 10px; }
QScrollBar::handle:horizontal { background: #2a3346; border-radius: 5px; min-width: 24px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""
