"""
Modern UI theme and styling system — v2 Professional
Provides consistent typography, spacing, colors, and component styles.
"""
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QColor, QFont
import os

# ── Spacing constants ──
SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 16
SPACING_LG = 24
SPACING_XL = 32

class ModernTheme:
    def __init__(self):
        self.colors = {
            'primary': '#2196F3',
            'primary_dark': '#1565C0',
            'primary_light': '#64B5F6',
            'secondary': '#4CAF50',
            'accent': '#03DAC6',
            'background': '#0F0F0F',
            'surface': '#1A1A1A',
            'surface_variant': '#242424',
            'card': '#1E1E1E',
            'card_hover': '#2A2A2A',
            'border': '#333333',
            'border_light': '#444444',
            'text': '#ECECEC',
            'text_secondary': '#999999',
            'text_disabled': '#555555',
            'on_surface': '#D0D0D0',
            'error': '#CF6679',
            'error_dark': '#B00020',
            'warning': '#FFB74D',
            'success': '#66BB6A',
            'success_dark': '#388E3C',
            'info': '#42A5F5',
        }

    # ── Application theme ──

    def apply_theme(self, app):
        """Apply modern dark theme to application"""
        selection_fix = self._selection_visibility_stylesheet()
        try:
            import qdarkstyle
            base = qdarkstyle.load_stylesheet_pyside6()
            app.setStyleSheet(base + "\n" + selection_fix)
            return True
        except ImportError:
            self._apply_custom_dark_theme(app)
            app.setStyleSheet(app.styleSheet() + "\n" + selection_fix)
            return False

    def _apply_custom_dark_theme(self, app):
        app.setStyle('Fusion')
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(15, 15, 15))
        palette.setColor(QPalette.WindowText, QColor(236, 236, 236))
        palette.setColor(QPalette.Base, QColor(26, 26, 26))
        palette.setColor(QPalette.AlternateBase, QColor(36, 36, 36))
        palette.setColor(QPalette.ToolTipBase, QColor(30, 30, 30))
        palette.setColor(QPalette.ToolTipText, QColor(236, 236, 236))
        palette.setColor(QPalette.Text, QColor(236, 236, 236))
        palette.setColor(QPalette.Button, QColor(36, 36, 36))
        palette.setColor(QPalette.ButtonText, QColor(236, 236, 236))
        palette.setColor(QPalette.BrightText, QColor(255, 82, 82))
        palette.setColor(QPalette.Link, QColor(33, 150, 243))
        palette.setColor(QPalette.Highlight, QColor(33, 150, 243))
        palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
        app.setPalette(palette)

    def _selection_visibility_stylesheet(self):
        return f"""
            QComboBox QAbstractItemView {{
                background-color: {self.colors['surface_variant']};
                color: {self.colors['text']};
                selection-background-color: {self.colors['primary']};
                selection-color: #FFFFFF;
            }}
            QComboBox {{ color: {self.colors['text']}; }}
            QComboBox:focus {{ color: {self.colors['text']}; }}
            QCheckBox {{ color: {self.colors['on_surface']}; }}
            QCheckBox:checked {{ color: {self.colors['text']}; }}
            QListWidget, QTreeWidget {{
                color: {self.colors['text']};
                selection-background-color: {self.colors['primary']};
                selection-color: #FFFFFF;
            }}
            QSpinBox {{ color: {self.colors['text']}; }}
            QLineEdit, QTextEdit {{
                color: {self.colors['text']};
                selection-background-color: {self.colors['primary']};
                selection-color: #FFFFFF;
            }}
        """

    # ── Typography ──

    def get_label_style(self, variant='body'):
        """Consistent label styles. Variants: title, heading, body, caption, mono"""
        styles = {
            'title': f"color: {self.colors['text']}; font-size: 20px; font-weight: 700; letter-spacing: 0.5px;",
            'heading': f"color: {self.colors['text']}; font-size: 15px; font-weight: 600;",
            'body': f"color: {self.colors['on_surface']}; font-size: 13px;",
            'caption': f"color: {self.colors['text_secondary']}; font-size: 11px;",
            'mono': f"color: {self.colors['accent']}; font-size: 12px; font-family: 'Consolas', 'JetBrains Mono', monospace;",
        }
        return styles.get(variant, styles['body'])

    # ── Status badges ──

    def get_status_badge_style(self, status='info'):
        """Compact inline status badge. Statuses: connected, disconnected, processing, error, info"""
        color_map = {
            'connected': (self.colors['success'], '#1B3A1E'),
            'disconnected': (self.colors['text_secondary'], self.colors['surface_variant']),
            'processing': (self.colors['warning'], '#3A2E1B'),
            'error': (self.colors['error'], '#3A1B1E'),
            'info': (self.colors['info'], '#1B2A3A'),
        }
        fg, bg = color_map.get(status, color_map['info'])
        return f"""
            QLabel {{
                color: {fg};
                background-color: {bg};
                border: 1px solid {fg};
                border-radius: 10px;
                padding: 3px 12px;
                font-size: 11px;
                font-weight: 600;
            }}
        """

    # ── Buttons ──

    def get_button_style(self, button_type='primary'):
        base = f"""
            QPushButton {{
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-weight: 600;
                font-size: 13px;
                min-height: 18px;
            }}
            QPushButton:disabled {{
                opacity: 0.5;
                background-color: {self.colors['surface_variant']};
                color: {self.colors['text_disabled']};
            }}
        """
        variants = {
            'primary': f"""
                QPushButton {{ background-color: {self.colors['primary']}; color: white; }}
                QPushButton:hover {{ background-color: {self.colors['primary_dark']}; }}
                QPushButton:pressed {{ background-color: #0D47A1; }}
            """,
            'success': f"""
                QPushButton {{ background-color: {self.colors['success_dark']}; color: white; }}
                QPushButton:hover {{ background-color: #2E7D32; }}
                QPushButton:pressed {{ background-color: #1B5E20; }}
            """,
            'danger': f"""
                QPushButton {{ background-color: {self.colors['error_dark']}; color: white; }}
                QPushButton:hover {{ background-color: #8B0000; }}
                QPushButton:pressed {{ background-color: #5F0000; }}
            """,
            'secondary': f"""
                QPushButton {{ background-color: {self.colors['surface_variant']}; color: {self.colors['text']}; border: 1px solid {self.colors['border']}; }}
                QPushButton:hover {{ background-color: {self.colors['card_hover']}; border-color: {self.colors['border_light']}; }}
            """,
            'ghost': f"""
                QPushButton {{ background-color: transparent; color: {self.colors['primary']}; }}
                QPushButton:hover {{ background-color: rgba(33, 150, 243, 0.1); }}
            """,
        }
        return base + variants.get(button_type, variants['primary'])

    # ── Inputs ──

    def get_input_style(self):
        return f"""
            QLineEdit, QTextEdit, QComboBox {{
                border: 1px solid {self.colors['border']};
                border-radius: 6px;
                padding: 10px 12px;
                font-size: 13px;
                background-color: {self.colors['surface']};
                color: {self.colors['text']};
            }}
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
                border-color: {self.colors['primary']};
            }}
        """

    # ── Progress bar ──

    def get_progress_bar_style(self):
        return f"""
            QProgressBar {{
                border: none;
                border-radius: 4px;
                background-color: {self.colors['surface_variant']};
                text-align: center;
                font-weight: 600;
                font-size: 12px;
                color: {self.colors['text']};
                min-height: 20px;
                max-height: 20px;
            }}
            QProgressBar::chunk {{
                border-radius: 4px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {self.colors['primary']}, stop:1 {self.colors['accent']});
            }}
        """

    # ── Cards ──

    def get_card_style(self):
        return f"""
            QFrame {{
                background-color: {self.colors['card']};
                border: 1px solid {self.colors['border']};
                border-radius: 10px;
            }}
            QFrame:hover {{
                border-color: {self.colors['primary']};
                background-color: {self.colors['card_hover']};
            }}
        """

    def get_anime_card_style(self):
        return f"""
            ModernAnimeCard {{
                border: 1px solid transparent;
                border-radius: 10px;
                background-color: {self.colors['card']};
            }}
            ModernAnimeCard:hover {{
                border-color: {self.colors['primary']};
                background-color: {self.colors['card_hover']};
            }}
        """

    def get_anime_card_hover_style(self):
        return f"""
            ModernAnimeCard {{
                border: 1px solid {self.colors['primary']};
                border-radius: 10px;
                background-color: {self.colors['card_hover']};
            }}
        """

    # ── Log area ──

    def get_log_style(self):
        return f"""
            QTextEdit {{
                background-color: #0A0E14;
                border: 1px solid {self.colors['border']};
                border-radius: 6px;
                padding: 10px;
                font-family: 'Consolas', 'JetBrains Mono', 'Monaco', monospace;
                font-size: 12px;
                line-height: 1.5;
                color: {self.colors['on_surface']};
            }}
        """

    # ── Group box ──

    def get_group_box_style(self):
        return f"""
            QGroupBox {{
                font-weight: 600;
                font-size: 14px;
                border: 1px solid {self.colors['border']};
                border-radius: 8px;
                margin-top: 12px;
                padding: 16px 12px 12px 12px;
                background-color: {self.colors['surface']};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 6px;
                color: {self.colors['primary']};
                background-color: {self.colors['surface']};
            }}
        """

    # ── Tab widget ──

    def get_tab_style(self):
        return f"""
            QTabWidget::pane {{
                border: 1px solid {self.colors['border']};
                background-color: {self.colors['surface']};
                border-radius: 0 0 8px 8px;
                top: -1px;
            }}
            QTabBar::tab {{
                background-color: {self.colors['surface_variant']};
                color: {self.colors['text_secondary']};
                padding: 10px 24px;
                margin-right: 1px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 600;
                font-size: 13px;
                border: 1px solid {self.colors['border']};
                border-bottom: none;
            }}
            QTabBar::tab:selected {{
                background-color: {self.colors['surface']};
                color: {self.colors['primary']};
                border-bottom: 2px solid {self.colors['primary']};
            }}
            QTabBar::tab:hover:!selected {{
                background-color: {self.colors['card']};
                color: {self.colors['text']};
            }}
        """

    # ── Scrollbar ──

    def get_scrollbar_style(self):
        return f"""
            QScrollBar:vertical {{
                background-color: transparent;
                width: 8px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background-color: {self.colors['border_light']};
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background-color: {self.colors['text_secondary']};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """

    # ── Collapsible section header ──

    def get_collapsible_header_style(self):
        return f"""
            QPushButton {{
                background-color: {self.colors['surface_variant']};
                color: {self.colors['text']};
                border: 1px solid {self.colors['border']};
                border-radius: 6px;
                padding: 10px 14px;
                font-weight: 600;
                font-size: 13px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: {self.colors['card']};
                border-color: {self.colors['border_light']};
            }}
        """

    # ── Splitter ──

    def get_splitter_style(self):
        return f"""
            QSplitter::handle {{
                background-color: {self.colors['border']};
                height: 2px;
            }}
            QSplitter::handle:hover {{
                background-color: {self.colors['primary']};
            }}
        """

    # ── Main window base ──

    def get_main_window_style(self):
        return f"""
            QMainWindow {{
                background-color: {self.colors['background']};
                color: {self.colors['text']};
            }}
            QScrollArea {{
                border: none;
                background-color: transparent;
            }}
            QStatusBar {{
                background-color: {self.colors['surface']};
                color: {self.colors['text_secondary']};
                border-top: 1px solid {self.colors['border']};
                font-size: 12px;
                padding: 4px 12px;
            }}
            {self.get_group_box_style()}
            {self.get_input_style()}
            {self.get_tab_style()}
            {self.get_scrollbar_style()}
            {self.get_splitter_style()}
        """


def setup_fonts(app):
    """Setup modern fonts for the application"""
    fonts = ['Segoe UI', 'SF Pro Display', 'Helvetica Neue', 'Arial']
    for font_name in fonts:
        font = QFont(font_name, 10)
        if font.exactMatch():
            app.setFont(font)
            break


def get_icon_path(icon_name):
    """Get path to icon file"""
    base_path = os.path.dirname(os.path.dirname(__file__))
    icon_path = os.path.join(base_path, 'images', f'{icon_name}.png')
    if os.path.exists(icon_path):
        return icon_path
    return None
