"""Tema visual de ModHub (oscuro, pensado para pasar horas con él)."""
from __future__ import annotations

COLORS = {
    "bg": "#14161b", "panel": "#1b1e25", "panel2": "#222631", "sidebar": "#0f1115", "border": "#2b303c",
    "text": "#e7e9ee", "muted": "#98a1b3", "accent": "#5b8cff", "accent_hover": "#7aa2ff",
    "ok": "#3ecf8e", "warn": "#f5a524", "error": "#f05454", "info": "#5b8cff", "adult": "#e2609b",
}

SOURCE_COLORS = {
    "nexus": "#d98f40", "modrinth": "#1bd96a", "thunderstore": "#3fa9f5", "curseforge": "#f16436",
    "modio": "#44d7b6", "gamebanana": "#f5d142", "steam": "#66c0f4", "moddb": "#c94141",
    "ayakamods": "#b07cf0", "local": "#98a1b3",
}
SOURCE_NAMES = {
    "nexus": "Nexus", "modrinth": "Modrinth", "thunderstore": "Thunderstore", "curseforge": "CurseForge",
    "modio": "mod.io", "gamebanana": "GameBanana", "steam": "Steam", "moddb": "Mod DB",
    "ayakamods": "AyakaMods", "local": "Local",
}
POLICY_COLORS = {"automatic": COLORS["ok"], "browser_intercept": COLORS["warn"], "manual_stream": "#66c0f4"}

C = COLORS
STYLESHEET = f"""
* {{ font-family: "Segoe UI", "Inter", "Noto Sans", sans-serif; font-size: 10pt; color: {C['text']}; }}
QMainWindow, QDialog, QWidget#Page {{ background: {C['bg']}; }}
QWidget#Sidebar {{ background: {C['sidebar']}; border-right: 1px solid {C['border']}; }}
QLabel#Logo {{ font-size: 17pt; font-weight: 700; padding: 18px 16px 4px 16px; }}
QLabel#LogoSub {{ color: {C['muted']}; padding: 0 16px 14px 16px; font-size: 9pt; }}
QListWidget#Nav {{ background: transparent; border: none; outline: none; padding: 4px 8px; }}
QListWidget#Nav::item {{ padding: 10px 12px; border-radius: 8px; margin: 2px 0; color: {C['muted']}; }}
QListWidget#Nav::item:hover {{ background: {C['panel']}; color: {C['text']}; }}
QListWidget#Nav::item:selected {{ background: {C['panel2']}; color: {C['text']}; border-left: 3px solid {C['accent']}; }}
QLabel#PageTitle {{ font-size: 16pt; font-weight: 600; }}
QLabel#PageSubtitle, QLabel.muted, QLabel#Muted {{ color: {C['muted']}; }}
QLabel#SectionTitle {{ font-size: 11pt; font-weight: 600; padding-top: 6px; }}
QFrame#Card, QFrame#FilterPanel, QFrame#DetailPanel {{
    background: {C['panel']}; border: 1px solid {C['border']}; border-radius: 10px; }}
QPushButton {{ background: {C['panel2']}; border: 1px solid {C['border']}; border-radius: 7px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {C['accent']}; }}
QPushButton:pressed {{ background: {C['border']}; }}
QPushButton:disabled {{ color: #5d6575; border-color: {C['panel2']}; }}
QPushButton#Primary {{ background: {C['accent']}; border-color: {C['accent']}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {C['accent_hover']}; }}
QPushButton#Primary:disabled {{ background: {C['panel2']}; border-color: {C['border']}; color: #6b7385; }}
QPushButton#Danger {{ border-color: {C['error']}; color: {C['error']}; }}
QPushButton#Danger:hover {{ background: {C['error']}; color: white; }}
QPushButton#Danger:disabled {{ border-color: {C['panel2']}; color: #5d6575; }}
QPushButton#Icon {{ padding: 3px; min-width: 28px; max-width: 30px; }}
QPushButton:checked {{ background: {C['accent']}; color: white; border-color: {C['accent']}; }}
QLineEdit, QComboBox, QSpinBox, QTextBrowser, QPlainTextEdit {{
    background: {C['panel2']}; border: 1px solid {C['border']}; border-radius: 7px; padding: 6px 8px;
    selection-background-color: {C['accent']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {C['accent']}; }}
QLineEdit#SearchBox {{ font-size: 11pt; padding: 9px 12px; }}
QComboBox QAbstractItemView {{ background: {C['panel2']}; border: 1px solid {C['border']};
    selection-background-color: {C['accent']}; }}
QTableWidget, QTreeWidget, QListWidget {{ background: {C['panel']}; border: 1px solid {C['border']};
    border-radius: 10px; gridline-color: {C['border']}; alternate-background-color: #1f232b; }}
QTableWidget::item, QTreeWidget::item {{ padding: 4px; }}
QTableWidget::item:selected, QTreeWidget::item:selected, QListWidget::item:selected {{ background: #2c3a5c; }}
QHeaderView::section {{ background: {C['panel2']}; color: {C['muted']}; border: none;
    border-bottom: 1px solid {C['border']}; padding: 7px; font-weight: 600; }}
QProgressBar {{ background: {C['panel2']}; border: 1px solid {C['border']}; border-radius: 6px; height: 16px;
    text-align: center; font-size: 8.5pt; }}
QProgressBar::chunk {{ background: {C['accent']}; border-radius: 5px; }}
QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QGroupBox {{ border: 1px solid {C['border']}; border-radius: 10px; margin-top: 14px; padding: 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {C['muted']}; font-weight: 600; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget#qt_scrollarea_viewport {{ background: transparent; }}
QWidget#ScrollBody {{ background: {C['bg']}; }}
QRadioButton:disabled, QCheckBox:disabled {{ color: #6b7385; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {C['border']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QTabBar#SourceTabs {{ background: transparent; }}
QTabBar#SourceTabs::tab {{ background: {C['panel']}; color: {C['muted']}; border: 1px solid {C['border']};
    border-radius: 8px; padding: 6px 14px; margin: 4px 6px 6px 0; }}
QTabBar#SourceTabs::tab:hover {{ color: {C['text']}; border-color: {C['accent']}; }}
QTabBar#SourceTabs::tab:selected {{ background: {C['accent']}; color: white; border-color: {C['accent']}; }}
QWidget#DetailInner {{ background: transparent; }}
QListWidget#Gallery {{ background: transparent; border: none; }}
QListWidget#Gallery::item {{ border: 1px solid {C['border']}; border-radius: 6px; margin: 2px; }}
QListWidget#Gallery::item:selected {{ border-color: {C['accent']}; background: transparent; }}
QStatusBar {{ background: {C['sidebar']}; color: {C['muted']}; border-top: 1px solid {C['border']}; }}
QToolTip {{ background: {C['panel2']}; color: {C['text']}; border: 1px solid {C['border']}; padding: 6px; }}
QFrame#Banner {{ border-radius: 8px; border: 1px solid {C['border']}; background: {C['panel2']}; }}
QFrame#Banner[level="ok"] {{ border-color: {C['ok']}; }}
QFrame#Banner[level="warn"] {{ border-color: {C['warn']}; }}
QFrame#Banner[level="error"] {{ border-color: {C['error']}; }}
QFrame#Banner[level="info"] {{ border-color: {C['info']}; }}
"""
