import sys
import json
import os
from pathlib import Path
from PySide6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QWidget,
                              QHBoxLayout, QLabel, QLineEdit, QPushButton,
                              QScrollArea, QGridLayout, QFrame, QProgressBar,
                              QTextEdit, QTabWidget, QGroupBox,
                              QMessageBox, QStatusBar, QComboBox,
                              QTreeWidget, QTreeWidgetItem, QDialog, QDialogButtonBox,
                              QMenu)
from PySide6.QtCore import QThread, Signal, Qt, QTimer, QSize, QPoint
from PySide6.QtGui import QPixmap, QPainter, QBrush, QPen, QAction, QIcon, QKeySequence, QShortcut
from typing import Dict, List, Any, Optional
from .sonarr_client import SonarrClient
from .processor import VideoProcessor
from .logger import Logger
from .webhook_server import WebhookManager
from .modern_theme import ModernTheme, setup_fonts, get_icon_path
from .translation_tracker import TranslationTracker
from .image_loader import ImageLoader
from .anime_card import ModernAnimeCard
from .anime_grid import ModernAnimeGrid
from .episode_dialog import EpisodeDetailsDialog
from .processing_worker import ProcessingWorker


class SonarrSubtitleTranslator(QMainWindow):
    """Main application window for Sonarr Subtitle Translator"""

    def __init__(self):
        super().__init__()

        # Initialize modern theme
        self.theme = ModernTheme()

        self.setWindowTitle('Sonarr Subtitle Translator v3.0')
        self.setGeometry(100, 100, 1400, 900)
        self.setMinimumSize(1200, 800)

        # Set application icon
        icon_path = get_icon_path('icon')
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

        self.config_file = 'config.json'
        self.sonarr_client: Optional[SonarrClient] = None
        self.translation_tracker = TranslationTracker()
        self.series_cards: List[ModernAnimeCard] = []
        self.worker: Optional[ProcessingWorker] = None
        self.webhook_manager = WebhookManager()

        # Cache system
        self.series_cache: Dict[str, Any] = {}
        self.cache_timestamp: Optional[float] = None
        self.cache_duration: int = 300  # 5 minutes

        # Processing queue
        self.processing_queue: List[Dict[str, Any]] = []
        self.start_time: Optional[float] = None

        self.setup_ui()
        self.setup_shortcuts()
        self.apply_modern_styling()
        self.load_config()
        self.start_webhook_server()

        # Auto-test API after UI is ready
        QTimer.singleShot(2000, self.auto_test_api)

    def setup_shortcuts(self) -> None:
        """Setup modern keyboard shortcuts with tooltips"""
        # F5 - Refresh library
        refresh_shortcut = QShortcut(QKeySequence("F5"), self)
        refresh_shortcut.activated.connect(self.refresh_series_list)

        # Ctrl+R - Alternative refresh
        QShortcut(QKeySequence("Ctrl+R"), self, self.refresh_series_list)

        # Ctrl+F - Focus search
        search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        search_shortcut.activated.connect(lambda: self.search_box.setFocus() if hasattr(self, 'search_box') else None)

        # Ctrl+L - Clear logs
        QShortcut(QKeySequence("Ctrl+L"), self, lambda: self.log_area.clear() if hasattr(self, 'log_area') else None)

        # Ctrl+Enter - Start processing
        start_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        start_shortcut.activated.connect(self.start_processing)

        # Esc - Stop processing
        stop_shortcut = QShortcut(QKeySequence("Escape"), self)
        stop_shortcut.activated.connect(self.stop_processing)

        # Tab switching shortcuts
        QShortcut(QKeySequence("Ctrl+1"), self, lambda: self.tab_widget.setCurrentIndex(0))
        QShortcut(QKeySequence("Ctrl+2"), self, lambda: self.tab_widget.setCurrentIndex(1))
        QShortcut(QKeySequence("Ctrl+3"), self, lambda: self.tab_widget.setCurrentIndex(2))

    def apply_modern_styling(self) -> None:
        """Apply modern theme styling to the entire application"""
        # Apply main window styling
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {self.theme.colors['background']};
                color: {self.theme.colors['text']};
            }}
            {self.theme.get_group_box_style()}
            {self.theme.get_input_style()}
            QSplitter::handle {{
                background-color: #3A3A3A;
                width: 2px;
            }}
            QScrollArea {{
                border: none;
                background-color: transparent;
            }}
            QScrollBar:vertical {{
                background-color: {self.theme.colors['surface']};
                width: 12px;
                border-radius: 6px;
            }}
            QScrollBar::handle:vertical {{
                background-color: #555;
                border-radius: 6px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background-color: #777;
            }}
            QTabWidget::pane {{
                border: 1px solid #3A3A3A;
                background-color: {self.theme.colors['surface']};
                border-radius: 8px;
            }}
            QTabBar::tab {{
                background-color: {self.theme.colors['card']};
                color: {self.theme.colors['text']};
                padding: 12px 20px;
                margin-right: 2px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                font-weight: 600;
            }}
            QTabBar::tab:selected {{
                background-color: {self.theme.colors['primary']};
                color: white;
            }}
            QTabBar::tab:hover {{
                background-color: {self.theme.colors['primary_dark']};
            }}
        """)

    def setup_ui(self) -> None:
        """Setup the main UI"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage('Ready')

        # Main layout
        main_layout = QVBoxLayout(central_widget)

        # Tab widget
        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget)

        self.create_series_tab()
        self.create_processing_tab()
        self.create_settings_tab()

    def create_series_tab(self) -> None:
        """Create the series/library tab"""
        tab = QWidget()
        self.tab_widget.addTab(tab, "🎬 Anime Library")

        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)

        # Top controls bar
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(20, 10, 20, 10)

        # Refresh button with modern styling and icon
        self.refresh_btn = QPushButton('🔄 Refresh Library')
        self.refresh_btn.setFixedHeight(40)
        self.refresh_btn.setStyleSheet(self.theme.get_button_style('success'))
        self.refresh_btn.setToolTip('Refresh series list from Sonarr (F5)')

        # Try to set icon
        icon_path = get_icon_path('refresh')
        if icon_path:
            self.refresh_btn.setIcon(QIcon(icon_path))
            self.refresh_btn.setIconSize(QSize(20, 20))

        self.refresh_btn.clicked.connect(self.refresh_series_list)
        controls_layout.addWidget(self.refresh_btn)

        # Search box with modern styling
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText('🔍 Search anime...')
        self.search_box.setToolTip('Search by title, year, network, or genre (Ctrl+F)')
        self.search_box.setFixedHeight(40)
        self.search_box.setMaximumWidth(350)
        self.search_box.setStyleSheet(self.theme.get_input_style())
        self.search_box.textChanged.connect(self.filter_series)
        controls_layout.addWidget(self.search_box)

        controls_layout.addStretch()

        # Series count label
        self.series_count_label = QLabel('No series loaded')
        self.series_count_label.setStyleSheet("color: #cccccc; font-size: 12px; font-weight: bold;")
        controls_layout.addWidget(self.series_count_label)

        layout.addLayout(controls_layout)

        # Modern anime grid
        self.anime_grid = ModernAnimeGrid(self)
        self.anime_grid.quick_translate.connect(self.quick_translate_series)
        self.anime_grid.show_episodes.connect(self.show_episode_details)

        layout.addWidget(self.anime_grid)

    def create_processing_tab(self) -> None:
        """Create the processing tab"""
        tab = QWidget()
        self.tab_widget.addTab(tab, "⚙️ Processing")

        layout = QVBoxLayout(tab)

        # API selection and status
        api_group = QGroupBox("Translation Configuration")
        api_layout = QGridLayout(api_group)

        api_layout.addWidget(QLabel('API:'), 0, 0)
        self.api_combo = QComboBox()
        self.api_combo.addItems(['LibreTranslate', 'Google', 'Ollama', 'GPT', 'DeepL', 'Gemini'])
        self.api_combo.currentTextChanged.connect(self.auto_test_api)
        api_layout.addWidget(self.api_combo, 0, 1)

        # API Status indicator
        self.api_status_label = QLabel('❓ API Status: Unknown')
        self.api_status_label.setStyleSheet("color: #cccccc; font-size: 11px;")
        api_layout.addWidget(self.api_status_label, 0, 2)

        # Test API button (now manual only)
        self.test_api_btn = QPushButton('🔄 Manual Test')
        self.test_api_btn.setFixedHeight(30)
        self.test_api_btn.clicked.connect(self.test_current_api)
        api_layout.addWidget(self.test_api_btn, 1, 0)

        # Queue status
        self.queue_label = QLabel('Queue: 0 items')
        self.queue_label.setStyleSheet("color: #cccccc; font-size: 11px;")
        api_layout.addWidget(self.queue_label, 1, 1, 1, 2)

        layout.addWidget(api_group)

        # Processing controls with modern buttons and icons
        controls_group = QGroupBox("Controls")
        controls_layout = QHBoxLayout(controls_group)

        self.start_processing_btn = QPushButton('▶️ Start Processing Selected')
        self.start_processing_btn.setStyleSheet(self.theme.get_button_style('success'))

        # Try to set play icon
        icon_path = get_icon_path('play')
        if icon_path:
            self.start_processing_btn.setIcon(QIcon(icon_path))
            self.start_processing_btn.setIconSize(QSize(20, 20))

        self.start_processing_btn.clicked.connect(self.start_processing)
        controls_layout.addWidget(self.start_processing_btn)

        self.stop_processing_btn = QPushButton('⏹️ Stop Processing')
        self.stop_processing_btn.setStyleSheet(self.theme.get_button_style('danger'))

        # Try to set stop icon
        icon_path = get_icon_path('stop')
        if icon_path:
            self.stop_processing_btn.setIcon(QIcon(icon_path))
            self.stop_processing_btn.setIconSize(QSize(20, 20))

        self.stop_processing_btn.clicked.connect(self.stop_processing)
        self.stop_processing_btn.setEnabled(False)
        controls_layout.addWidget(self.stop_processing_btn)

        # Clear logs button
        self.clear_logs_btn = QPushButton('🧹 Clear Logs')
        self.clear_logs_btn.setStyleSheet(self.theme.get_button_style('secondary'))
        self.clear_logs_btn.clicked.connect(lambda: self.log_area.clear())
        controls_layout.addWidget(self.clear_logs_btn)

        layout.addWidget(controls_group)

        # Modern progress bar with ETA
        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet(self.theme.get_progress_bar_style())
        self.progress_bar.setFormat("%p% - Processing...")
        progress_layout.addWidget(self.progress_bar)

        self.eta_label = QLabel('ETA: --')
        self.eta_label.setStyleSheet(f"""
            QLabel {{
                color: {self.theme.colors['text_secondary']};
                font-size: 14px;
                font-weight: 600;
                padding: 8px;
                background-color: {self.theme.colors['surface']};
                border-radius: 6px;
                min-width: 100px;
            }}
        """)
        progress_layout.addWidget(self.eta_label)

        layout.addLayout(progress_layout)

        # Modern log area with monospace font and colors
        log_group = QGroupBox("Processing Log")
        log_layout = QVBoxLayout(log_group)

        self.log_area = QTextEdit()
        self.log_area.setMaximumHeight(200)
        self.log_area.setStyleSheet(self.theme.get_log_style())
        self.log_area.textChanged.connect(self.limit_log_size)
        log_layout.addWidget(self.log_area)

        layout.addWidget(log_group)

        # Translation display
        trans_group = QGroupBox("Real-time Translation")
        trans_layout = QHBoxLayout(trans_group)

        # Original text with label
        orig_layout = QVBoxLayout()
        orig_layout.addWidget(QLabel('Original:'))
        self.original_text = QTextEdit()
        self.original_text.setMaximumHeight(150)
        orig_layout.addWidget(self.original_text)
        trans_layout.addLayout(orig_layout)

        # Translated text with label
        trans_text_layout = QVBoxLayout()
        trans_text_layout.addWidget(QLabel('Translated:'))
        self.translated_text = QTextEdit()
        self.translated_text.setMaximumHeight(150)
        trans_text_layout.addWidget(self.translated_text)
        trans_layout.addLayout(trans_text_layout)

        layout.addWidget(trans_group)

    def start_webhook_server(self) -> None:
        """Start webhook server automatically"""
        try:
            if not self.webhook_manager.is_available():
                self.status_bar.showMessage('Webhook disabled - Flask not installed (pip install flask)')
                return

            keys = {
                'deepl': getattr(self, 'deepl_key', QLineEdit()).text(),
                'gpt': getattr(self, 'gpt_key', QLineEdit()).text(),
                'gemini': getattr(self, 'gemini_key', QLineEdit()).text(),
                'ollama_url': getattr(self, 'ollama_url', QLineEdit()).text() or 'http://localhost:11434',
                'ollama_model': getattr(self, 'ollama_model', QLineEdit()).text() or 'qwen2.5:7b'
            }

            api_type = getattr(self, 'api_combo', None)
            api_type = api_type.currentText() if api_type else 'Ollama'

            server = self.webhook_manager.start_webhook(8080, keys, api_type)
            if server:
                self.status_bar.showMessage('🌐 Webhook server running on port 8080 - Test: http://localhost:8080/test')
                # Add webhook info to log
                if hasattr(self, 'log_area'):
                    self.log_area.append('🌐 Webhook server started on port 8080')
                    self.log_area.append('🔗 Webhook URL: http://localhost:8080/webhook/sonarr')
                    self.log_area.append('🧪 Test URL: http://localhost:8080/test')
            else:
                self.status_bar.showMessage('Webhook disabled - Flask not available')
        except Exception as e:
            self.status_bar.showMessage(f'Webhook server failed: {e}')
            if hasattr(self, 'log_area'):
                self.log_area.append(f'❌ Webhook server error: {e}')

    def create_settings_tab(self) -> None:
        """Create the settings tab"""
        tab = QWidget()
        self.tab_widget.addTab(tab, "⚙️ Settings")

        layout = QVBoxLayout(tab)

        # Sonarr Connection (moved from separate tab)
        conn_group = QGroupBox("Sonarr Configuration")
        conn_layout = QGridLayout(conn_group)

        conn_layout.addWidget(QLabel('Sonarr URL:'), 0, 0)
        self.sonarr_url = QLineEdit('http://localhost:8989')
        conn_layout.addWidget(self.sonarr_url, 0, 1)

        conn_layout.addWidget(QLabel('API Key:'), 1, 0)
        self.sonarr_api_key = QLineEdit()
        self.sonarr_api_key.setEchoMode(QLineEdit.Password)
        conn_layout.addWidget(self.sonarr_api_key, 1, 1)

        self.test_btn = QPushButton('🔍 Test Connection')
        self.test_btn.setStyleSheet(self.theme.get_button_style('primary'))
        self.test_btn.clicked.connect(self.test_sonarr_connection)
        conn_layout.addWidget(self.test_btn, 2, 0)

        self.load_series_btn = QPushButton('📚 Load Series')
        self.load_series_btn.setStyleSheet(self.theme.get_button_style('success'))
        self.load_series_btn.clicked.connect(self.load_series)
        self.load_series_btn.setEnabled(False)
        conn_layout.addWidget(self.load_series_btn, 2, 1)

        # Connection status
        self.connection_status = QLabel('❓ Not connected')
        self.connection_status.setStyleSheet("font-size: 14px; padding: 10px;")
        conn_layout.addWidget(self.connection_status, 3, 0, 1, 2)

        layout.addWidget(conn_group)

        # API Keys
        keys_group = QGroupBox("Translation API Keys")
        keys_layout = QGridLayout(keys_group)

        keys_layout.addWidget(QLabel('DeepL:'), 0, 0)
        self.deepl_key = QLineEdit()
        self.deepl_key.setEchoMode(QLineEdit.Password)
        self.deepl_key.textChanged.connect(self.auto_test_api)
        keys_layout.addWidget(self.deepl_key, 0, 1)

        keys_layout.addWidget(QLabel('GPT:'), 1, 0)
        self.gpt_key = QLineEdit()
        self.gpt_key.setEchoMode(QLineEdit.Password)
        self.gpt_key.textChanged.connect(self.auto_test_api)
        keys_layout.addWidget(self.gpt_key, 1, 1)

        keys_layout.addWidget(QLabel('Gemini:'), 2, 0)
        self.gemini_key = QLineEdit()
        self.gemini_key.setEchoMode(QLineEdit.Password)
        self.gemini_key.textChanged.connect(self.auto_test_api)
        keys_layout.addWidget(self.gemini_key, 2, 1)

        keys_layout.addWidget(QLabel('Ollama URL:'), 3, 0)
        self.ollama_url = QLineEdit('http://localhost:11434')
        self.ollama_url.textChanged.connect(self.auto_test_api)
        keys_layout.addWidget(self.ollama_url, 3, 1)

        keys_layout.addWidget(QLabel('Ollama Model:'), 4, 0)
        self.ollama_model = QLineEdit('qwen2.5:14b-instruct-q4_K_M')
        self.ollama_model.textChanged.connect(self.auto_test_api)
        keys_layout.addWidget(self.ollama_model, 4, 1)

        layout.addWidget(keys_group)

        # Webhook Configuration
        webhook_group = QGroupBox("Webhook Configuration")
        webhook_layout = QVBoxLayout(webhook_group)

        webhook_url_label = QLabel('Webhook URL for Sonarr:')
        webhook_layout.addWidget(webhook_url_label)

        self.webhook_url_display = QLineEdit('http://localhost:8080/webhook/sonarr')
        self.webhook_url_display.setReadOnly(True)
        self.webhook_url_display.setStyleSheet("background-color: #2d2d2d; color: #4CAF50;")
        webhook_layout.addWidget(self.webhook_url_display)

        webhook_info = QLabel('Add this URL to Sonarr Settings → Connect → Webhook')
        webhook_info.setStyleSheet("color: #cccccc; font-size: 11px;")
        webhook_layout.addWidget(webhook_info)

        layout.addWidget(webhook_group)

        layout.addStretch()

    def test_sonarr_connection(self) -> None:
        """Test Sonarr connection"""
        url = self.sonarr_url.text().strip()
        api_key = self.sonarr_api_key.text().strip()

        if not url or not api_key:
            QMessageBox.warning(self, "Error", "Please enter Sonarr URL and API Key")
            return

        self.test_btn.setEnabled(False)
        self.test_btn.setText('Testing...')

        try:
            self.sonarr_client = SonarrClient(url, api_key)
            if self.sonarr_client.test_connection():
                self.connection_status.setText('✅ Connected to Sonarr')
                self.connection_status.setStyleSheet("color: #4CAF50; font-size: 14px; padding: 10px;")
                self.load_series_btn.setEnabled(True)
                self.save_config()
            else:
                self.connection_status.setText('❌ Connection failed')
                self.connection_status.setStyleSheet("color: #f44336; font-size: 14px; padding: 10px;")
                self.sonarr_client = None
        except Exception as e:
            self.connection_status.setText(f'❌ Error: {str(e)}')
            self.connection_status.setStyleSheet("color: #f44336; font-size: 14px; padding: 10px;")
            self.sonarr_client = None

        self.test_btn.setEnabled(True)
        self.test_btn.setText('🔍 Test Connection')

    def load_series(self) -> None:
        """Load series from Sonarr"""
        if not self.sonarr_client:
            return

        # Check cache first
        import time
        current_time = time.time()
        if (self.cache_timestamp and
            current_time - self.cache_timestamp < self.cache_duration and
            self.series_cache):
            self.populate_series_grid(self.series_cache)
            self.status_bar.showMessage(f'Loaded {len(self.series_cache)} series (cached)')
            return

        self.load_series_btn.setEnabled(False)
        self.load_series_btn.setText('Loading...')

        try:
            series_data = self.sonarr_client.get_series_with_files()

            # Add translation statistics to each series
            for series in series_data:
                episodes = self.sonarr_client.get_episodes(series['id'])
                episode_files = self.sonarr_client.get_episode_files(series['id'])
                file_lookup = {f['id']: f for f in episode_files}

                episodes_with_files = []
                for ep in episodes:
                    if ep.get('hasFile') and ep.get('episodeFileId'):
                        file_data = file_lookup.get(ep['episodeFileId'])
                        if file_data:
                            episodes_with_files.append({
                                'id': ep['id'],
                                'video_path': file_data.get('path')
                            })

                stats = self.translation_tracker.get_series_stats(series['id'], episodes_with_files)
                series['translation_stats'] = stats

            # Update cache
            self.series_cache = series_data
            self.cache_timestamp = current_time

            self.populate_series_grid(series_data)
            self.status_bar.showMessage(f'Loaded {len(series_data)} series')
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load series: {str(e)}")

        self.load_series_btn.setEnabled(True)
        self.load_series_btn.setText('📚 Load Series')

    def limit_log_size(self) -> None:
        """Limit log size to prevent memory issues"""
        if self.log_area.document().lineCount() > 1000:
            # Keep last 500 lines
            cursor = self.log_area.textCursor()
            cursor.movePosition(cursor.Start)
            for _ in range(500):
                cursor.movePosition(cursor.Down)
            cursor.movePosition(cursor.Start, cursor.KeepAnchor)
            cursor.removeSelectedText()

            # Add truncation notice
            cursor.movePosition(cursor.Start)
            cursor.insertText("[Log truncated - showing last 500 lines]\n")

    def auto_test_api(self) -> None:
        """Auto test API when settings change"""
        if not hasattr(self, 'auto_test_timer'):
            self.auto_test_timer = QTimer()
            self.auto_test_timer.setSingleShot(True)
            self.auto_test_timer.timeout.connect(self.test_current_api)

        # Delay test by 1 second to avoid testing while user is typing
        self.auto_test_timer.start(1000)

    def test_current_api(self) -> None:
        """Test the currently selected API"""
        api_type = self.api_combo.currentText()
        self.api_status_label.setText('⏳ Testing...')

        # Simple test based on API type
        try:
            if api_type == 'Ollama':
                import requests
                url = self.ollama_url.text() or 'http://localhost:11434'
                response = requests.get(f"{url}/api/tags", timeout=5)
                if response.status_code == 200:
                    self.api_status_label.setText('✅ Ollama: Connected')
                    self.api_status_label.setStyleSheet("color: #4CAF50; font-size: 11px;")
                else:
                    raise Exception("Connection failed")

            elif api_type == 'DeepL':
                if not self.deepl_key.text().strip():
                    raise Exception("API key required")
                self.api_status_label.setText('✅ DeepL: Key provided')
                self.api_status_label.setStyleSheet("color: #4CAF50; font-size: 11px;")

            elif api_type in ['GPT', 'Gemini']:
                key_field = self.gpt_key if api_type == 'GPT' else self.gemini_key
                if not key_field.text().strip():
                    raise Exception("API key required")
                self.api_status_label.setText(f'✅ {api_type}: Key provided')
                self.api_status_label.setStyleSheet("color: #4CAF50; font-size: 11px;")

            elif api_type == 'LibreTranslate':
                try:
                    import requests
                    response = requests.post('https://libretranslate.com/translate', {
                        'q': 'test',
                        'source': 'en',
                        'target': 'pt',
                        'format': 'text'
                    }, timeout=10)
                    if response.status_code == 200:
                        self.api_status_label.setText('✅ LibreTranslate: Connected')
                        self.api_status_label.setStyleSheet("color: #4CAF50; font-size: 11px;")
                    else:
                        raise Exception("Connection failed")
                except Exception as e:
                    self.api_status_label.setText(f'❌ {api_type}: {str(e)}')
                    self.api_status_label.setStyleSheet("color: #f44336; font-size: 11px;")

            else:  # Google
                self.api_status_label.setText('✅ Google: Available')
                self.api_status_label.setStyleSheet("color: #4CAF50; font-size: 11px;")

        except Exception as e:
            self.api_status_label.setText(f'❌ {api_type}: {str(e)}')
            self.api_status_label.setStyleSheet("color: #f44336; font-size: 11px;")

    def refresh_series_list(self) -> None:
        """Manually refresh the series list from Sonarr"""
        if not self.sonarr_client:
            QMessageBox.warning(self, "Warning", "Please connect to Sonarr first in Settings tab")
            return

        # Clear cache to force refresh
        self.series_cache = {}
        self.cache_timestamp = None

        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText('🔄 Refreshing...')
        self.status_bar.showMessage('Refreshing series list...')

        try:
            series_data = self.sonarr_client.get_series_with_files()

            # Add translation statistics to new series only
            for series in series_data:
                # Check if we have cached data for this series
                series_id = series.get('id')
                needs_analysis = True

                for cached_series in self.series_cache.values() if isinstance(self.series_cache, dict) else self.series_cache:
                    if isinstance(cached_series, dict) and cached_series.get('id') == series_id:
                        if 'translation_stats' in cached_series:
                            series['translation_stats'] = cached_series['translation_stats']
                            needs_analysis = False
                            break

                if needs_analysis:
                    episodes = self.sonarr_client.get_episodes(series['id'])
                    episode_files = self.sonarr_client.get_episode_files(series['id'])
                    file_lookup = {f['id']: f for f in episode_files}

                    episodes_with_files = []
                    for ep in episodes:
                        if ep.get('hasFile') and ep.get('episodeFileId'):
                            file_data = file_lookup.get(ep['episodeFileId'])
                            if file_data:
                                episodes_with_files.append({
                                    'id': ep['id'],
                                    'video_path': file_data.get('path')
                                })

                    stats = self.translation_tracker.get_series_stats(series['id'], episodes_with_files)
                    series['translation_stats'] = stats

            # Update cache
            import time
            self.series_cache = series_data
            self.cache_timestamp = time.time()

            self.populate_series_grid(series_data)
            self.status_bar.showMessage(f'Refreshed: {len(series_data)} series loaded')
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to refresh series: {str(e)}")
            self.status_bar.showMessage('Refresh failed')
        finally:
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText('🔄 Refresh Library')

    def populate_series_grid(self, series_data: List[Dict[str, Any]]) -> None:
        """Populate the series grid with data"""
        # Store original data for filtering
        self.original_series_data = series_data

        # Use the modern anime grid
        self.anime_grid.populate_series(series_data)

        # Update series count
        self.series_count_label.setText(f'{len(series_data)} series loaded')

        # Switch to anime library tab after loading
        self.tab_widget.setCurrentIndex(0)

    def filter_series(self, search_text: str) -> None:
        """Filter series based on search text"""
        if not hasattr(self, 'original_series_data'):
            return

        if not search_text.strip():
            # Show all series if search is empty
            filtered_data = self.original_series_data
        else:
            # Filter by title, year, network, or genres
            search_lower = search_text.lower()
            filtered_data = []

            for series in self.original_series_data:
                # Search in title
                if search_lower in series['title'].lower():
                    filtered_data.append(series)
                    continue

                # Search in year
                if series.get('year') and search_lower in str(series['year']):
                    filtered_data.append(series)
                    continue

                # Search in network
                if series.get('network') and search_lower in series['network'].lower():
                    filtered_data.append(series)
                    continue

                # Search in genres
                if series.get('genres'):
                    for genre in series['genres']:
                        if search_lower in genre.lower():
                            filtered_data.append(series)
                            break

        # Update grid with filtered data
        self.anime_grid.populate_series(filtered_data)

        # Update count
        if search_text.strip():
            self.series_count_label.setText(f'{len(filtered_data)} of {len(self.original_series_data)} series')
        else:
            self.series_count_label.setText(f'{len(filtered_data)} series loaded')

    def start_processing(self) -> None:
        """Start processing (placeholder - processing is now handled directly from anime cards)"""
        pass

    def stop_processing(self) -> None:
        """Stop processing"""
        if self.worker:
            self.worker.stop()
            # Stop the processor directly
            if hasattr(self.worker, 'processor'):
                self.worker.processor.stop_processing()
            # Also stop the translator if it exists
            if hasattr(self.worker, 'processor') and hasattr(self.worker.processor, 'translator'):
                self.worker.processor.translator.stop_translation()
        self.processing_finished()

    def processing_finished(self) -> None:
        """Handle processing completion"""
        self.start_processing_btn.setEnabled(True)
        self.stop_processing_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.eta_label.setText('ETA: --')
        self.log_area.append("🎉 Processing completed!")

        # Clear queue and reset timing
        self.processing_queue = []
        self.start_time = None

        # Finish processing indicator on anime card
        if hasattr(self, 'worker') and self.worker and len(self.worker.selected_series) == 1:
            series_id = self.worker.selected_series[0].get('id')
            if series_id:
                self.anime_grid.finish_processing_series(series_id)

    def update_translation_display(self, original: str, translated: str, api: str) -> None:
        """Update translation display"""
        self.original_text.append(f"[{api}] {original}")
        self.translated_text.append(f"[{api}] {translated}")

    def load_config(self) -> None:
        """Load configuration from file"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)

                self.sonarr_url.setText(config.get('sonarr_url', 'http://localhost:8989'))
                self.sonarr_api_key.setText(config.get('sonarr_api_key', ''))
                self.deepl_key.setText(config.get('deepl_key', ''))
                self.gpt_key.setText(config.get('gpt_key', ''))
                self.gemini_key.setText(config.get('gemini_key', ''))
                self.ollama_url.setText(config.get('ollama_url', 'http://localhost:11434'))
                self.ollama_model.setText(config.get('ollama_model', 'qwen2.5:14b-instruct-q4_K_M'))
                self.api_combo.setCurrentText(config.get('api', 'LibreTranslate'))

                # Auto-connect if credentials exist
                if config.get('sonarr_url') and config.get('sonarr_api_key'):
                    QTimer.singleShot(1000, self.auto_connect_sonarr)
            except:
                pass

    def save_config(self) -> None:
        """Save configuration to file"""
        config = {
            'sonarr_url': self.sonarr_url.text(),
            'sonarr_api_key': self.sonarr_api_key.text(),
            'deepl_key': self.deepl_key.text(),
            'gpt_key': self.gpt_key.text(),
            'gemini_key': self.gemini_key.text(),
            'ollama_url': self.ollama_url.text(),
            'ollama_model': self.ollama_model.text(),
            'api': self.api_combo.currentText()
        }

        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}")

    def auto_connect_sonarr(self) -> None:
        """Auto-connect to Sonarr if credentials exist"""
        try:
            url = self.sonarr_url.text().strip()
            api_key = self.sonarr_api_key.text().strip()

            if url and api_key:
                self.status_bar.showMessage('Auto-connecting to Sonarr...')
                self.sonarr_client = SonarrClient(url, api_key)

                if self.sonarr_client.test_connection():
                    self.connection_status.setText('✅ Auto-connected to Sonarr')
                    self.connection_status.setStyleSheet("color: #4CAF50; font-size: 14px; padding: 10px;")
                    self.load_series_btn.setEnabled(True)

                    # Auto-load series
                    QTimer.singleShot(500, self.auto_load_series)
                else:
                    self.connection_status.setText('❌ Auto-connect failed')
                    self.connection_status.setStyleSheet("color: #f44336; font-size: 14px; padding: 10px;")
        except Exception as e:
            self.connection_status.setText(f'❌ Auto-connect error: {str(e)}')
            self.connection_status.setStyleSheet("color: #f44336; font-size: 14px; padding: 10px;")

    def auto_load_series(self) -> None:
        """Auto-load series after connection"""
        try:
            if self.sonarr_client:
                self.status_bar.showMessage('Loading series...')
                series_data = self.sonarr_client.get_series_with_files()

                # Add translation statistics
                for series in series_data:
                    episodes = self.sonarr_client.get_episodes(series['id'])
                    episode_files = self.sonarr_client.get_episode_files(series['id'])
                    file_lookup = {f['id']: f for f in episode_files}

                    episodes_with_files = []
                    for ep in episodes:
                        if ep.get('hasFile') and ep.get('episodeFileId'):
                            file_data = file_lookup.get(ep['episodeFileId'])
                            if file_data:
                                episodes_with_files.append({
                                    'id': ep['id'],
                                    'video_path': file_data.get('path')
                                })

                    stats = self.translation_tracker.get_series_stats(series['id'], episodes_with_files)
                    series['translation_stats'] = stats

                self.populate_series_grid(series_data)
                self.status_bar.showMessage(f'Auto-loaded {len(series_data)} series')
                # Switch to anime library tab after loading
                self.tab_widget.setCurrentIndex(0)
        except Exception as e:
            self.status_bar.showMessage(f'Auto-load failed: {str(e)}')

    def show_episode_details(self, series_data: Dict[str, Any]) -> None:
        """Show detailed episode view for a series"""
        if not self.sonarr_client:
            QMessageBox.warning(self, "Error", "Not connected to Sonarr")
            return

        dialog = EpisodeDetailsDialog(series_data, self.sonarr_client, self.translation_tracker, self)
        dialog.exec()

    def quick_translate_series(self, series_data: Dict[str, Any]) -> None:
        """Quick translate entire series"""
        reply = QMessageBox.question(
            self,
            "Quick Translate",
            f"Translate entire series: {series_data['title']}?\n\nThis will process all available episodes.",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.start_single_series_processing([series_data])

    def start_episode_processing(self, episodes: List[Dict[str, Any]], series_data: Dict[str, Any]) -> None:
        """Start processing specific episodes"""
        # Convert episode data to file paths for processing
        file_paths = []
        for ep_data in episodes:
            file_info = ep_data.get('file_data')
            if file_info and file_info.get('path'):
                file_paths.append(file_info['path'])

        if not file_paths:
            QMessageBox.warning(self, "Warning", "No valid episode files found")
            return

        # Create a temporary series data for processing
        temp_series = {
            'title': series_data['title'],
            'path': str(Path(file_paths[0]).parent),
            'files': file_paths
        }

        self.start_single_series_processing([temp_series], specific_files=file_paths)

    def start_single_series_processing(self, series_list: List[Dict[str, Any]], specific_files: Optional[List[str]] = None) -> None:
        """Start processing for single series or episodes"""
        keys = {
            'deepl': self.deepl_key.text(),
            'gpt': self.gpt_key.text(),
            'gemini': self.gemini_key.text(),
            'ollama_url': self.ollama_url.text(),
            'ollama_model': self.ollama_model.text()
        }

        # Update processing queue and start time
        self.processing_queue = series_list.copy()
        import time
        self.start_time = time.time()
        self.update_queue_status()

        # Start processing indicator on anime card
        if series_list and len(series_list) == 1:
            series_id = series_list[0].get('id')
            if series_id:
                self.anime_grid.start_processing_series(series_id)

        self.worker = ProcessingWorker(series_list, keys, self.api_combo.currentText(), specific_files)
        self.worker.progress.connect(self.update_processing_progress)
        self.worker.log_update.connect(self.log_area.append)
        self.worker.translation_update.connect(self.update_translation_display)
        self.worker.finished.connect(self.processing_finished)

        self.worker.start()

        # Switch to processing tab
        self.tab_widget.setCurrentIndex(1)

        self.start_processing_btn.setEnabled(False)
        self.stop_processing_btn.setEnabled(True)

    def update_processing_progress(self, progress: float) -> None:
        """Update progress bar and anime card progress"""
        self.progress_bar.setValue(int(progress))

        # Calculate and update ETA
        if self.start_time and progress > 0:
            import time
            elapsed = time.time() - self.start_time
            if progress > 5:  # Only calculate ETA after 5% to avoid wild estimates
                total_estimated = elapsed * (100 / progress)
                remaining = total_estimated - elapsed

                if remaining > 0:
                    if remaining < 60:
                        eta_text = f"ETA: {int(remaining)}s"
                    elif remaining < 3600:
                        eta_text = f"ETA: {int(remaining/60)}m {int(remaining%60)}s"
                    else:
                        hours = int(remaining / 3600)
                        minutes = int((remaining % 3600) / 60)
                        eta_text = f"ETA: {hours}h {minutes}m"

                    self.eta_label.setText(eta_text)
                else:
                    self.eta_label.setText("ETA: --")
            else:
                self.eta_label.setText("ETA: Calculating...")

        # Update anime card progress if processing single series
        if hasattr(self, 'worker') and self.worker and len(self.worker.selected_series) == 1:
            series_id = self.worker.selected_series[0].get('id')
            if series_id:
                self.anime_grid.update_series_progress(series_id, progress)

    def update_queue_status(self) -> None:
        """Update queue status display"""
        pass  # Not used in current implementation


def run_sonarr_gui() -> None:
    """Run the Sonarr Subtitle Translator GUI application"""
    app = QApplication(sys.argv)

    # Apply modern theme to entire application
    theme = ModernTheme()
    theme.apply_theme(app)
    setup_fonts(app)

    # Set application icon globally
    icon_path = get_icon_path('icon')
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

    window = SonarrSubtitleTranslator()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    # Add current directory to path for imports
    current_dir = Path(__file__).parent
    if str(current_dir) not in sys.path:
        sys.path.insert(0, str(current_dir))

    run_sonarr_gui()
