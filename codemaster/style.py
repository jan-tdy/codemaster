"""The app's single dark-theme stylesheet."""

STYLE = """
QMainWindow, QWidget { background: #14161c; color: #e7e9ee;
    font-family: 'Segoe UI', 'Noto Sans', sans-serif; font-size: 13px; }
#Header { background: #1b1e26; border-bottom: 1px solid #2a2e3a; }
#HeaderTitle { font-size: 18px; font-weight: 700; }
#Search { background: #262b36; border: 1px solid #333a48; border-radius: 8px;
    padding: 7px 12px; color: #e7e9ee; }
#Search:focus { border: 1px solid #4f8cff; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; color: #9aa3b2; padding: 10px 18px;
    border: none; font-weight: 600; }
QTabBar::tab:selected { color: #e7e9ee; border-bottom: 2px solid #4f8cff; }
QTabBar::tab:hover { color: #e7e9ee; }
#Page { background: #14161c; border: none; }
#Sidebar { background: #171a21; border: none; border-right: 1px solid #262b36;
    padding: 8px 0; outline: none; }
#Sidebar::item { padding: 9px 18px; color: #b6bdca; border: none; }
#Sidebar::item:selected { background: #1f2430; color: #e7e9ee;
    border-left: 3px solid #4f8cff; }
#Sidebar::item:hover { color: #e7e9ee; }
#AppTile { background: #1b1e26; border: 1px solid #262b36; border-radius: 14px; }
#AppTile:hover { border: 1px solid #3a4458; }
#TileTitle { font-size: 14px; font-weight: 700; }
#TileMeta { color: #8b93a4; font-size: 11px; }
#TileBadges { color: #ffb347; font-size: 11px; font-weight: 600; }
#AppTitle { font-size: 20px; font-weight: 700; }
#AppMeta { color: #8b93a4; font-size: 12px; }
#AppDesc { color: #b6bdca; }
#Section { font-size: 13px; font-weight: 700; color: #7f8aa0;
    text-transform: uppercase; letter-spacing: 1px; margin-top: 8px; }
#Placeholder { color: #7f8aa0; font-size: 14px; padding: 60px; }
#BadgeWarn { background: #4a3214; color: #ffb347; border-radius: 6px;
    padding: 1px 8px; font-size: 11px; font-weight: 600; }
QPushButton#Primary { background: #4f8cff; color: white; border: none;
    border-radius: 8px; padding: 8px 16px; font-weight: 600; }
QPushButton#Primary:hover { background: #3d7bf0; }
QPushButton#Primary:disabled { background: #2f3947; color: #8b93a4; }
QPushButton#Ghost { background: transparent; color: #cdd3df;
    border: 1px solid #333a48; border-radius: 8px; padding: 8px 16px; }
QPushButton#Ghost:hover { border: 1px solid #4f8cff; color: #fff; }
QTextEdit, QLineEdit { background: #1b1e26; border: 1px solid #2a2e3a;
    border-radius: 8px; padding: 8px; color: #e7e9ee; }
QComboBox { background: #1b1e26; border: 1px solid #2a2e3a; border-radius: 8px;
    padding: 6px 10px; color: #e7e9ee; }
QComboBox:hover { border: 1px solid #4f8cff; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #1b1e26; color: #e7e9ee;
    border: 1px solid #333a48; selection-background-color: #1f2430;
    selection-color: #e7e9ee; outline: none; }
QScrollBar:vertical { background: #14161c; width: 10px; }
QScrollBar::handle:vertical { background: #333a48; border-radius: 5px; }
QScrollBar:horizontal { background: #14161c; height: 10px; }
QScrollBar::handle:horizontal { background: #333a48; border-radius: 5px; }
QStatusBar { background: #1b1e26; color: #9aa3b2; }
"""
