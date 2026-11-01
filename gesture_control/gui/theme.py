"""Colours and the application style sheet."""

BG = "#14171a"
SIDEBAR = "#0f1214"
CARD = "#1c2024"
CARD_HOVER = "#23282d"
BORDER = "#2b3137"
TEXT = "#eef1f3"
MUTED = "#98a2ab"
ACCENT = "#3cd7c8"
ACCENT_DARK = "#10312e"
AMBER = "#f5af3c"
RED = "#ef5350"

STYLE = f"""
* {{ font-family: "Segoe UI"; font-size: 13px; color: {TEXT}; }}
QMainWindow, QDialog, QStackedWidget, QScrollArea, #page {{ background: {BG}; }}
QScrollArea {{ border: none; }}
QToolTip {{ background: {CARD}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}

#sidebar {{ background: {SIDEBAR}; }}
#brand {{ font-size: 15px; font-weight: 600; padding: 0 16px; }}
#navButton {{
    background: transparent; border: none; border-radius: 8px;
    padding: 10px 14px; text-align: left; color: {MUTED}; font-size: 14px;
}}
#navButton:hover {{ background: {CARD}; color: {TEXT}; }}
#navButton:checked {{ background: {ACCENT_DARK}; color: {ACCENT}; font-weight: 600; }}
#sidebarStatus {{ color: {MUTED}; padding: 0 16px; font-size: 12px; }}

#title {{ font-size: 22px; font-weight: 600; }}
#subtitle, #muted {{ color: {MUTED}; }}
#section {{ font-size: 14px; font-weight: 600; }}
#accentText {{ color: {ACCENT}; }}
#warning {{ color: {AMBER}; }}
#error {{ color: {RED}; }}
#rowTitle {{ font-size: 14px; font-weight: 600; }}

#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
#poseCard {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
#poseCard:hover {{ border-color: {MUTED}; }}
#poseCard[selected="true"] {{ border: 2px solid {ACCENT}; background: {ACCENT_DARK}; }}
#tileValue {{ font-size: 24px; font-weight: 600; }}
#tileLabel {{ color: {MUTED}; font-size: 12px; }}
#preview {{ background: #0b0d0f; border-radius: 8px; color: {MUTED}; }}
#miniView {{ background: {BG}; border: 1px solid {BORDER}; }}
#miniPicture {{ background: #0b0d0f; color: {MUTED}; }}
#miniTitle {{ font-size: 15px; font-weight: 600; }}
#miniTitle[tone="active"] {{ color: {ACCENT}; }}
#miniTitle[tone="paused"] {{ color: {AMBER}; }}
#miniDetail {{ color: {MUTED}; font-size: 12px; }}

QPushButton {{
    background: {CARD_HOVER}; border: 1px solid {BORDER}; border-radius: 7px;
    padding: 7px 14px; min-height: 18px;
}}
QPushButton:hover {{ border-color: {MUTED}; }}
QPushButton:disabled {{ color: #5f6972; border-color: {BORDER}; }}
QPushButton#primary {{ background: {ACCENT}; color: #06201d; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: #5ae3d6; }}
QPushButton#primary:disabled {{ background: {CARD_HOVER}; color: #5f6972; }}
QPushButton#startButton {{
    background: {ACCENT}; color: #06201d; border: none; font-weight: 700;
    font-size: 15px; padding: 10px 34px; border-radius: 9px;
}}
QPushButton#startButton:hover {{ background: #5ae3d6; }}
QPushButton#startButton[running="true"] {{ background: {RED}; color: white; }}
QPushButton#startButton[running="true"]:hover {{ background: #f26f6d; }}
QPushButton#startButton:disabled {{ background: {CARD_HOVER}; color: #5f6972; }}
QPushButton#danger {{ color: {RED}; }}
QPushButton#danger:hover {{ border-color: {RED}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 5px 8px; min-height: 18px; selection-background-color: {ACCENT_DARK};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {CARD}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_DARK};
}}
QMenu {{ background: {CARD}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT_DARK}; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QRadioButton {{ spacing: 8px; }}
QListWidget {{ background: transparent; border: none; color: {MUTED}; font-size: 12px; }}
QListWidget::item {{ padding: 3px 0; border: none; }}
QSlider::groove:horizontal {{ background: {BORDER}; height: 4px; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 14px; margin: -5px 0; border-radius: 7px; }}
QProgressBar {{ background: {BORDER}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""
