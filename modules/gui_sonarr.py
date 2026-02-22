import sys
import json
import os
import requests
import re
import subprocess
import tempfile
from pathlib import Path
from PySide6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QWidget,
                              QHBoxLayout, QLabel, QLineEdit, QPushButton,
                              QScrollArea, QGridLayout, QFrame, QProgressBar,
                              QTextEdit, QTabWidget, QGroupBox,
                              QMessageBox, QStatusBar, QComboBox,
                              QMenu, QSpinBox, QCheckBox, QFileDialog,
                              QSplitter, QSlider, QRadioButton, QButtonGroup,
                              QTreeWidget, QTreeWidgetItem, QHeaderView,
                              QSystemTrayIcon, QTableWidget, QTableWidgetItem,
                              QDialog, QDialogButtonBox)
from PySide6.QtCore import QThread, Signal, Qt, QTimer, QSize, QTime
from PySide6.QtGui import QPixmap, QPainter, QBrush, QPen, QAction, QIcon, QKeySequence, QShortcut
from .sonarr_client import SonarrClient
from .processor import VideoProcessor
from .logger import Logger
from .webhook_server import WebhookManager
from .modern_theme import ModernTheme, setup_fonts, get_icon_path, SPACING_SM, SPACING_MD, SPACING_LG
from .image_loader import ImageLoader
from .episode_dialog import EpisodeDetailsDialog
from .translation_tracker import TranslationTracker


class ModelDownloadWorker(QThread):
    """Worker for downloading Ollama models in background."""
    finished_signal = Signal(bool)
    log_signal = Signal(str)

    def __init__(self, ollama_url, model_name, hardware_detector):
        super().__init__()
        self.ollama_url = ollama_url
        self.model_name = model_name
        self.detector = hardware_detector

    def run(self):
        def progress_cb(progress, message):
            self.log_signal.emit(f"📥 {message}")
        try:
            success = self.detector.pull_model(
                self.ollama_url,
                self.model_name,
                progress_cb
            )
            self.finished_signal.emit(success)
        except Exception as e:
            self.log_signal.emit(f"❌ Erro: {e}")
            self.finished_signal.emit(False)


class ImportGGUFWorker(QThread):
    """Worker for creating Ollama model from local GGUF file."""
    finished_signal = Signal(bool, str)
    log_signal = Signal(str)

    def __init__(self, gguf_path: str, model_name: str = "qwen2.5:32b-instruct-q4_K_M"):
        super().__init__()
        self.gguf_path = gguf_path
        self.model_name = model_name

    def run(self):
        import re as _re
        modelfile_path = None
        try:
            # Validate model_name: only allow safe characters to prevent argument injection
            if not _re.match(r'^[a-zA-Z0-9_:.\-]+$', self.model_name):
                self.finished_signal.emit(False, f"Nome de modelo inválido: '{self.model_name}'. Use apenas letras, números, ':', '.', '-', '_'.")
                return

            # Sanitize gguf_path: strip newlines to prevent Modelfile directive injection
            safe_gguf = self.gguf_path.replace("\r", "").replace("\n", "").replace("\\", "/")
            content = f"FROM {safe_gguf}\n"
            fd, modelfile_path = tempfile.mkstemp(suffix=".modelfile", prefix="ollama_")
            try:
                os.write(fd, content.encode("utf-8"))
            finally:
                os.close(fd)
            self.log_signal.emit(f"📂 Criando modelo '{self.model_name}' a partir de {os.path.basename(self.gguf_path)}...")
            result = subprocess.run(
                ["ollama", "create", self.model_name, "-f", modelfile_path],
                capture_output=True, text=True, timeout=600,
                cwd=os.path.expanduser("~")
            )
            if modelfile_path and os.path.exists(modelfile_path):
                try:
                    os.unlink(modelfile_path)
                except OSError:
                    pass
            if result.returncode == 0:
                self.finished_signal.emit(True, f"Modelo '{self.model_name}' criado com sucesso!")
            else:
                err = (result.stderr or result.stdout or "").strip()
                self.finished_signal.emit(False, err or f"ollama create retornou código {result.returncode}")
        except subprocess.TimeoutExpired:
            if modelfile_path and os.path.exists(modelfile_path):
                try:
                    os.unlink(modelfile_path)
                except OSError:
                    pass
            self.finished_signal.emit(False, "Timeout ao criar o modelo (máx. 10 min).")
        except FileNotFoundError:
            self.finished_signal.emit(False, "Ollama não encontrado. Instale o Ollama e certifique-se de que 'ollama' está no PATH.")
        except Exception as e:
            if modelfile_path and os.path.exists(modelfile_path):
                try:
                    os.unlink(modelfile_path)
                except OSError:
                    pass
            self.finished_signal.emit(False, str(e))


class _LocalScanWorker(QThread):
    """Scans a folder for video/subtitle files in a background thread."""
    finished = Signal(list)

    def __init__(self, folder: str):
        super().__init__()
        self._folder = folder

    def run(self):
        import os
        VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.mov', '.wmv'}
        SUB_EXTS = {'.srt', '.ass', '.ssa', '.vtt'}
        results = []
        for root, _dirs, files in os.walk(self._folder):
            for fname in sorted(files):
                ext = os.path.splitext(fname)[1].lower()
                if ext in VIDEO_EXTS or ext in SUB_EXTS:
                    results.append(os.path.join(root, fname))
        self.finished.emit(results)


class ProcessingWorker(QThread):
    progress = Signal(float)
    log_update = Signal(str)
    translation_update = Signal(str, str, str)
    finished = Signal()
    # Emitted when review_before_save is on: (output_path, [(original, translated), ...])
    review_ready = Signal(str, list)

    def __init__(self, selected_series, keys, api_type, specific_files=None, track_map=None):
        super().__init__()
        self.selected_series = selected_series
        self.keys = keys
        self.api_type = api_type
        self.specific_files = specific_files
        self.track_map = track_map or {}
        self.stop_flag = False
        self.processor = None
        self._review_approval: dict = {}  # output_path -> approved lines or None (skip)

    def stop(self):
        self.stop_flag = True
        if self.processor:
            self.processor.stop_processing()

    def run(self):
        logger = Logger(callback=self.log_update.emit)

        for i, series in enumerate(self.selected_series):
            if self.stop_flag:
                break

            self.log_update.emit(f"🎬 Processando: {series['title']}")

            series_metadata = {
                'title': series.get('title', ''),
                'genres': series.get('genres', []),
                'characters': [],
                'series_type': '',
            }
            genres_lower = [g.lower() for g in series_metadata['genres']]
            anime_signals = {'animation', 'anime'}
            doc_signals = {'documentary', 'news', 'reality'}
            if any(g in anime_signals for g in genres_lower):
                series_metadata['series_type'] = 'anime'
            elif any(g in doc_signals for g in genres_lower):
                series_metadata['series_type'] = 'documentary'
            else:
                series_metadata['series_type'] = 'live_action'

            self.processor = VideoProcessor(
                series['path'],
                self.keys,
                logger,
                lambda p, idx=i: self.progress.emit(min(100.0, ((idx + p / 100) / len(self.selected_series)) * 100)),
                lambda: self.stop_flag,
                self.api_type,
                self.translation_update.emit,
                specific_files=self.specific_files,
                series_metadata=series_metadata,
                track_map=self.track_map,
            )
            if self.keys.get('review_before_save'):
                self.processor.review_callback = lambda path, lines: self.review_ready.emit(path, lines)
            self.processor.process_all()

        self.finished.emit()


# ─── Anime Card ──────────────────────────────────────────────────────────────

class ModernAnimeCard(QFrame):
    clicked = Signal(dict)
    quick_translate = Signal(dict)
    show_episodes = Signal(dict)

    _theme = ModernTheme()

    def __init__(self, series_data, parent=None):
        super().__init__(parent)
        self.series_data = series_data
        self.parent_window = parent
        self.is_hovered = False
        self.is_processing = False
        self.progress_value = 0
        self._setup_ui()

    def _setup_ui(self):
        parent_width = self.parent_window.width() if self.parent_window else 1200
        card_width = max(140, min(180, parent_width // 8))
        card_height = int(card_width * 1.4)

        self.setFixedSize(card_width, card_height)
        self.setCursor(Qt.PointingHandCursor)

        tooltip = f"{self.series_data['title']}\n{self.series_data['episodeCount']} episodes"
        if self.series_data.get('year'):
            tooltip += f" ({self.series_data['year']})"
        if self.series_data.get('network'):
            tooltip += f"\nNetwork: {self.series_data['network']}"
        stats = self.series_data.get('translation_stats')
        if stats and stats.get('total', 0) > 0:
            tooltip += f"\nTranslated: {stats['translated']}/{stats['total']} ({stats['percentage']}%)"
        self.setToolTip(tooltip)

        self.setStyleSheet(self._theme.get_anime_card_style())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Poster
        self.poster_container = QFrame()
        self.poster_container.setStyleSheet("QFrame { border-radius: 10px; background-color: transparent; }")
        poster_layout = QVBoxLayout(self.poster_container)
        poster_layout.setContentsMargins(0, 0, 0, 0)

        poster_height = card_height - 15
        self.poster_label = QLabel()
        self.poster_label.setFixedSize(card_width, poster_height)
        self.poster_label.setStyleSheet("border-radius: 8px; background-color: rgba(45, 45, 45, 100);")
        self.poster_label.setAlignment(Qt.AlignCenter)
        self.poster_label.setText("Loading...")
        self.poster_label.setScaledContents(True)
        poster_layout.addWidget(self.poster_label)

        # Overlay (on hover)
        self.overlay = QFrame(self.poster_container)
        self.overlay.setGeometry(0, 0, card_width, poster_height)
        self.overlay.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(0, 0, 0, 0), stop:0.7 rgba(0, 0, 0, 100), stop:1 rgba(0, 0, 0, 200));
                border-radius: 10px;
            }
        """)
        self.overlay.hide()

        overlay_layout = QVBoxLayout(self.overlay)
        overlay_layout.setContentsMargins(SPACING_SM, SPACING_SM, SPACING_SM, SPACING_SM)
        overlay_layout.addStretch()

        self.title_label = QLabel(self.series_data['title'])
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("color: white; font-weight: bold; font-size: 12px; background: transparent;")
        overlay_layout.addWidget(self.title_label)

        info_text = f"{self.series_data['episodeCount']} eps"
        if self.series_data.get('year'):
            info_text += f" · {self.series_data['year']}"
        if stats and stats.get('percentage', 0) > 0:
            info_text += f" · {stats['percentage']}%"

        self.info_label = QLabel(info_text)
        self.info_label.setStyleSheet("color: rgba(255,255,255,180); font-size: 9px; background: transparent;")
        overlay_layout.addWidget(self.info_label)

        # Processing progress bar (hidden)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{ border: none; background-color: rgba(255,255,255,50); border-radius: 2px; }}
            QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 {self._theme.colors['primary']}, stop:1 {self._theme.colors['accent']}); border-radius: 2px; }}
        """)
        self.progress_bar.hide()
        overlay_layout.addWidget(self.progress_bar)

        # Translation progress (always visible if available)
        if stats and stats.get('total', 0) > 0:
            self.translation_progress = QProgressBar()
            self.translation_progress.setFixedHeight(3)
            self.translation_progress.setTextVisible(False)
            self.translation_progress.setValue(int(stats.get('percentage', 0)))
            self.translation_progress.setStyleSheet(f"""
                QProgressBar {{ border: none; background-color: rgba(255,255,255,30); border-radius: 1px; }}
                QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {self._theme.colors['primary']}, stop:1 {self._theme.colors['accent']}); border-radius: 1px; }}
            """)
            overlay_layout.addWidget(self.translation_progress)

        layout.addWidget(self.poster_container)

        if self.series_data.get('poster'):
            self.image_loader = ImageLoader(self.series_data['id'], self.series_data['poster'])
            self.image_loader.image_loaded.connect(self.set_poster)
            self.image_loader.start()

    def enterEvent(self, event):
        self.is_hovered = True
        self.overlay.show()
        self.setStyleSheet(self._theme.get_anime_card_hover_style())
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.is_hovered = False
        if not self.is_processing:
            self.overlay.hide()
        self.setStyleSheet(self._theme.get_anime_card_style())
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.show_episodes.emit(self.series_data)
        super().mousePressEvent(event)

    def set_poster(self, series_id, pixmap):
        if series_id == self.series_data['id']:
            rounded = self._create_rounded_pixmap(pixmap, 8)
            self.poster_label.setPixmap(rounded)
            self.poster_label.setText("")

    @staticmethod
    def _create_rounded_pixmap(pixmap, radius):
        size = pixmap.size()
        rounded = QPixmap(size)
        rounded.fill(Qt.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(pixmap))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rounded.rect(), radius, radius)
        painter.end()
        return rounded

    def start_processing(self):
        self.is_processing = True
        self.progress_bar.show()
        self.overlay.show()
        self.progress_bar.setValue(0)

    def update_progress(self, value):
        self.progress_value = value
        self.progress_bar.setValue(int(value))

    def finish_processing(self):
        self.is_processing = False
        self.progress_bar.hide()
        if not self.is_hovered:
            self.overlay.hide()


# ─── Anime Grid ──────────────────────────────────────────────────────────────

class ModernAnimeGrid(QScrollArea):
    card_clicked = Signal(dict)
    quick_translate = Signal(dict)
    show_episodes = Signal(dict)

    _theme = ModernTheme()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.cards = []
        self._setup_ui()

    def _setup_ui(self):
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background-color: {self._theme.colors['background']};
            }}
            {self._theme.get_scrollbar_style()}
        """)
        self.container = QWidget()
        self.container.setStyleSheet("background: transparent;")
        self.grid_layout = QGridLayout(self.container)
        self.grid_layout.setSpacing(SPACING_MD)
        self.grid_layout.setContentsMargins(SPACING_LG, SPACING_LG, SPACING_LG, SPACING_LG)
        self.setWidget(self.container)

    def populate_series(self, series_data):
        self.clear_cards()
        window_width = self.width() if self.width() > 0 else 1200
        card_width = max(140, min(180, window_width // 8))
        cols = max(3, window_width // (card_width + SPACING_MD))

        for i, series in enumerate(series_data):
            card = ModernAnimeCard(series, self.parent_window)
            card.show_episodes.connect(self.show_episodes.emit)
            self.cards.append(card)
            self.grid_layout.addWidget(card, i // cols, i % cols)

        self.grid_layout.setRowStretch(len(series_data) // cols + 1, 1)
        self.grid_layout.setColumnStretch(cols, 1)

    def clear_cards(self):
        for card in self.cards:
            card.deleteLater()
        self.cards.clear()

    def get_card_by_series_id(self, series_id):
        for card in self.cards:
            if card.series_data['id'] == series_id:
                return card
        return None

    def start_processing_series(self, series_id):
        card = self.get_card_by_series_id(series_id)
        if card:
            card.start_processing()

    def update_series_progress(self, series_id, progress):
        card = self.get_card_by_series_id(series_id)
        if card:
            card.update_progress(progress)

    def finish_processing_series(self, series_id):
        card = self.get_card_by_series_id(series_id)
        if card:
            card.finish_processing()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'cards') and self.cards:
            series_data = [card.series_data for card in self.cards]
            self.populate_series(series_data)


# ─── Collapsible Section Widget ──────────────────────────────────────────────

class CollapsibleSection(QWidget):
    """A collapsible section with a toggle header."""
    def __init__(self, title: str, parent=None, collapsed: bool = True):
        super().__init__(parent)
        self._theme = ModernTheme()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.toggle_btn = QPushButton(f"▶  {title}")
        self.toggle_btn.setStyleSheet(self._theme.get_collapsible_header_style())
        self.toggle_btn.clicked.connect(self._toggle)
        layout.addWidget(self.toggle_btn)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(SPACING_SM, SPACING_SM, SPACING_SM, SPACING_SM)
        layout.addWidget(self.content)

        self._title = title
        if collapsed:
            self.content.hide()
        else:
            self.toggle_btn.setText(f"▼  {title}")

    def _toggle(self):
        visible = not self.content.isVisible()
        self.content.setVisible(visible)
        arrow = "▼" if visible else "▶"
        self.toggle_btn.setText(f"{arrow}  {self._title}")

    def add_widget(self, widget):
        self.content_layout.addWidget(widget)

    def add_layout(self, layout):
        self.content_layout.addLayout(layout)


# ─── Translation Review Dialog ───────────────────────────────────────────────

class TranslationReviewDialog(QDialog):
    """Dialog for reviewing and editing translations before saving.

    Supports a queue of files: the user can Save & Next through all of them.
    """
    saved = Signal(str, list)   # output_path, list of (original, translated) tuples
    skipped = Signal(str)       # output_path

    def __init__(self, review_queue: list, parent=None):
        """
        review_queue: list of dicts with keys:
            - output_path (str)
            - lines (list of [original, translated] pairs)
            - total (int) — total files in queue
            - index (int) — 0-based index in queue
        """
        super().__init__(parent)
        self._queue = review_queue
        self._current_idx = 0
        self.setMinimumSize(900, 600)
        self._setup_ui()
        self._load_current()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Title row
        title_row = QHBoxLayout()
        self._title_label = QLabel()
        self._title_label.setStyleSheet("font-size: 14px; font-weight: 600;")
        title_row.addWidget(self._title_label, 1)
        self._counter_label = QLabel()
        self._counter_label.setStyleSheet("color: gray; font-size: 12px;")
        title_row.addWidget(self._counter_label)
        layout.addLayout(title_row)

        # Search bar
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Filter lines...")
        self._search_input.textChanged.connect(self._filter_rows)
        search_row.addWidget(self._search_input, 1)
        layout.addLayout(search_row)

        # Table
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["#", "Original", "Translation"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.SelectedClicked)
        layout.addWidget(self._table, 1)

        # Buttons
        btn_row = QHBoxLayout()
        self._save_btn = QPushButton("Save")
        self._save_btn.setFixedHeight(38)
        self._save_btn.clicked.connect(self._save_current)
        btn_row.addWidget(self._save_btn)

        self._save_next_btn = QPushButton("Save && Next")
        self._save_next_btn.setFixedHeight(38)
        self._save_next_btn.clicked.connect(self._save_and_next)
        btn_row.addWidget(self._save_next_btn)

        self._skip_btn = QPushButton("Skip")
        self._skip_btn.setFixedHeight(38)
        self._skip_btn.clicked.connect(self._skip_current)
        btn_row.addWidget(self._skip_btn)

        btn_row.addStretch()

        self._close_btn = QPushButton("Close")
        self._close_btn.setFixedHeight(38)
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

    def _load_current(self):
        if self._current_idx >= len(self._queue):
            self.accept()
            return
        entry = self._queue[self._current_idx]
        total = entry.get('total', len(self._queue))
        idx = entry.get('index', self._current_idx)
        self.setWindowTitle(f"Review Translation — File {idx + 1} of {total}")
        self._title_label.setText(str(entry.get('output_path', '')))
        self._counter_label.setText(f"File {idx + 1} / {total}")
        self._search_input.clear()

        lines = entry.get('lines', [])
        self._table.setRowCount(len(lines))
        for row, (orig, trans) in enumerate(lines):
            num_item = QTableWidgetItem(str(row + 1))
            num_item.setFlags(num_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, num_item)
            orig_item = QTableWidgetItem(orig)
            orig_item.setFlags(orig_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 1, orig_item)
            self._table.setItem(row, 2, QTableWidgetItem(trans))

        # Update button labels
        is_last = self._current_idx >= len(self._queue) - 1
        self._save_next_btn.setEnabled(not is_last)
        self._save_next_btn.setText("Save && Next" if not is_last else "Save (last)")

    def _collect_lines(self):
        lines = []
        for row in range(self._table.rowCount()):
            orig = self._table.item(row, 1)
            trans = self._table.item(row, 2)
            lines.append((
                orig.text() if orig else "",
                trans.text() if trans else "",
            ))
        return lines

    def _filter_rows(self, text: str):
        text_lower = text.lower()
        for row in range(self._table.rowCount()):
            orig = self._table.item(row, 1)
            trans = self._table.item(row, 2)
            match = (
                not text_lower
                or (orig and text_lower in orig.text().lower())
                or (trans and text_lower in trans.text().lower())
            )
            self._table.setRowHidden(row, not match)

    def _save_current(self):
        entry = self._queue[self._current_idx]
        self.saved.emit(entry['output_path'], self._collect_lines())
        self.accept()

    def _save_and_next(self):
        entry = self._queue[self._current_idx]
        self.saved.emit(entry['output_path'], self._collect_lines())
        self._current_idx += 1
        self._load_current()

    def _skip_current(self):
        entry = self._queue[self._current_idx]
        self.skipped.emit(entry['output_path'])
        self._current_idx += 1
        self._load_current()


# ─── Main Window ─────────────────────────────────────────────────────────────

class SonarrSubtitleTranslator(QMainWindow):
    def __init__(self):
        super().__init__()
        self.theme = ModernTheme()
        self.setWindowTitle('Sonarr Subtitle Translator v3.0')
        self.setGeometry(100, 100, 1400, 900)
        self.setMinimumSize(1200, 800)

        icon_path = get_icon_path('icon')
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

        self.config_file = 'config.json'
        self.sonarr_client = None
        self.translation_tracker = TranslationTracker()
        self.series_cards = []
        self.worker = None
        self.webhook_manager = WebhookManager()

        try:
            from .hardware_detector import HardwareDetector
            self.hardware_detector = HardwareDetector()
        except Exception as e:
            self.hardware_detector = None
            print(f"Warning: Hardware detection failed: {e}")

        self.series_cache = {}
        self.cache_timestamp = None
        self.cache_duration = 300
        self.processing_queue = []
        self.start_time = None

        self.setup_ui()
        self.setup_shortcuts()
        self._apply_main_stylesheet()
        self.load_config()
        self.init_model_selector()
        self.display_hardware_info()
        self._setup_tray_icon()

        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    cfg = json.load(f)
                if cfg.get('enable_webhook', False):
                    self._start_webhook()
            except Exception:
                pass

        QTimer.singleShot(2000, self.auto_test_api)
        QTimer.singleShot(2500, self.update_cache_stats)

    # ── System Tray ──

    def _setup_tray_icon(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = None
            return

        self.tray_icon = QSystemTrayIcon(self)
        icon_path = get_icon_path('icon')
        if icon_path:
            self.tray_icon.setIcon(QIcon(icon_path))
        else:
            self.tray_icon.setIcon(self.style().standardIcon(
                self.style().StandardPixmap.SP_ComputerIcon
            ))

        tray_menu = QMenu(self)
        restore_action = QAction("Restore", self)
        restore_action.triggered.connect(self._restore_from_tray)
        tray_menu.addAction(restore_action)
        tray_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.quit)
        tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _restore_from_tray(self):
        self.showNormal()
        self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._restore_from_tray()

    def closeEvent(self, event):
        if self.tray_icon and self.tray_icon.isVisible():
            self.hide()
            self.tray_icon.showMessage(
                "Sonarr Subtitle Translator",
                "O programa continua em execução na bandeja do sistema.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
            event.ignore()
        else:
            event.accept()

    # ── Styling ──

    def _apply_main_stylesheet(self):
        self.setStyleSheet(self.theme.get_main_window_style())

    # ── Shortcuts ──

    def setup_shortcuts(self):
        QShortcut(QKeySequence("F5"), self, self.refresh_series_list)
        QShortcut(QKeySequence("Ctrl+R"), self, self.refresh_series_list)
        QShortcut(QKeySequence("Ctrl+F"), self,
                  lambda: self.search_box.setFocus() if hasattr(self, 'search_box') else None)
        QShortcut(QKeySequence("Ctrl+L"), self,
                  lambda: self.log_area.clear() if hasattr(self, 'log_area') else None)
        QShortcut(QKeySequence("Escape"), self, self.stop_processing)
        QShortcut(QKeySequence("Ctrl+1"), self, lambda: self.tab_widget.setCurrentIndex(0))
        QShortcut(QKeySequence("Ctrl+2"), self, lambda: self.tab_widget.setCurrentIndex(1))
        QShortcut(QKeySequence("Ctrl+3"), self, lambda: self.tab_widget.setCurrentIndex(2))

    # ── UI Setup ──

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage('Ready')

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget)

        self._create_series_tab()
        self._create_processing_tab()
        self._create_settings_tab()
        self._create_local_files_tab()

    # ── Series Tab ──

    def _create_series_tab(self):
        tab = QWidget()
        self.tab_widget.addTab(tab, "📺  Series Library")

        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar
        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background-color: {self.theme.colors['surface']};
                border-bottom: 1px solid {self.theme.colors['border']};
            }}
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(SPACING_LG, SPACING_MD, SPACING_LG, SPACING_MD)

        # App title
        title = QLabel("Series Library")
        title.setStyleSheet(self.theme.get_label_style('title'))
        header_layout.addWidget(title)

        # Sonarr connection badge
        self.sonarr_badge = QLabel("Disconnected")
        self.sonarr_badge.setStyleSheet(self.theme.get_status_badge_style('disconnected'))
        header_layout.addWidget(self.sonarr_badge)

        header_layout.addStretch()

        # Refresh button
        self.refresh_btn = QPushButton('Refresh')
        self.refresh_btn.setFixedHeight(36)
        self.refresh_btn.setStyleSheet(self.theme.get_button_style('secondary'))
        self.refresh_btn.setToolTip('Refresh series list from Sonarr (F5)')
        icon_p = get_icon_path('refresh')
        if icon_p:
            self.refresh_btn.setIcon(QIcon(icon_p))
            self.refresh_btn.setIconSize(QSize(16, 16))
        self.refresh_btn.clicked.connect(self.refresh_series_list)
        header_layout.addWidget(self.refresh_btn)

        # Search
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText('Search series...')
        self.search_box.setToolTip('Search by title, year, network, or genre (Ctrl+F)')
        self.search_box.setFixedHeight(36)
        self.search_box.setFixedWidth(280)
        self.search_box.textChanged.connect(self.filter_series)
        header_layout.addWidget(self.search_box)

        # Filter indicator
        self.filter_indicator = QLabel("")
        self.filter_indicator.setStyleSheet(self.theme.get_label_style('caption'))
        self.filter_indicator.hide()
        header_layout.addWidget(self.filter_indicator)

        # Series count
        self.series_count_label = QLabel('No series loaded')
        self.series_count_label.setStyleSheet(self.theme.get_label_style('caption'))
        header_layout.addWidget(self.series_count_label)

        layout.addWidget(header)

        # Grid
        self.anime_grid = ModernAnimeGrid(self)
        self.anime_grid.quick_translate.connect(self.quick_translate_series)
        self.anime_grid.show_episodes.connect(self.show_episode_details)
        layout.addWidget(self.anime_grid)

    # ── Processing Tab ──

    def _create_processing_tab(self):
        tab = QWidget()
        self.tab_widget.addTab(tab, "⚙️  Processing")

        layout = QVBoxLayout(tab)
        layout.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        layout.setSpacing(SPACING_SM)

        # Compact status bar
        status_frame = QFrame()
        status_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {self.theme.colors['surface']};
                border: 1px solid {self.theme.colors['border']};
                border-radius: 8px;
                padding: 4px;
            }}
        """)
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(SPACING_MD, SPACING_SM, SPACING_MD, SPACING_SM)

        self.api_status_label = QLabel('Ollama: Unknown')
        self.api_status_label.setStyleSheet(self.theme.get_label_style('body'))
        status_layout.addWidget(self.api_status_label)

        self.model_info_label = QLabel('Model: —')
        self.model_info_label.setStyleSheet(self.theme.get_label_style('caption'))
        status_layout.addWidget(self.model_info_label)

        self.cache_stats_label = QLabel('Cache: —')
        self.cache_stats_label.setStyleSheet(self.theme.get_label_style('caption'))
        status_layout.addWidget(self.cache_stats_label)

        status_layout.addStretch()

        self.test_api_btn = QPushButton('Test')
        self.test_api_btn.setFixedSize(70, 28)
        self.test_api_btn.setStyleSheet(self.theme.get_button_style('ghost'))
        self.test_api_btn.clicked.connect(self.test_current_api)
        status_layout.addWidget(self.test_api_btn)

        self.clear_cache_btn = QPushButton('Clear Cache')
        self.clear_cache_btn.setFixedSize(100, 28)
        self.clear_cache_btn.setStyleSheet(self.theme.get_button_style('ghost'))
        self.clear_cache_btn.setToolTip('Remove all cached translations')
        self.clear_cache_btn.clicked.connect(self.clear_translation_cache)
        status_layout.addWidget(self.clear_cache_btn)

        layout.addWidget(status_frame)

        # Controls row
        ctrl_row = QHBoxLayout()

        self.stop_processing_btn = QPushButton('⏹  Stop')
        self.stop_processing_btn.setStyleSheet(self.theme.get_button_style('danger'))
        self.stop_processing_btn.setFixedHeight(36)
        self.stop_processing_btn.clicked.connect(self.stop_processing)
        self.stop_processing_btn.setEnabled(False)
        ctrl_row.addWidget(self.stop_processing_btn)

        self.clear_logs_btn = QPushButton('Clear Logs')
        self.clear_logs_btn.setStyleSheet(self.theme.get_button_style('secondary'))
        self.clear_logs_btn.setFixedHeight(36)
        self.clear_logs_btn.clicked.connect(lambda: self.log_area.clear())
        ctrl_row.addWidget(self.clear_logs_btn)

        ctrl_row.addStretch()

        self.eta_label = QLabel('ETA: —')
        self.eta_label.setStyleSheet(f"""
            QLabel {{
                {self.theme.get_label_style('mono')}
                padding: 6px 14px;
                background-color: {self.theme.colors['surface']};
                border-radius: 6px;
                border: 1px solid {self.theme.colors['border']};
            }}
        """)
        ctrl_row.addWidget(self.eta_label)

        layout.addLayout(ctrl_row)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet(self.theme.get_progress_bar_style())
        self.progress_bar.setFormat("%p%")
        layout.addWidget(self.progress_bar)

        # Splitter: Log (top) + Translation Preview (bottom)
        splitter = QSplitter(Qt.Vertical)
        splitter.setStyleSheet(self.theme.get_splitter_style())

        # Log area
        log_frame = QFrame()
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(0, 0, 0, 0)

        log_header = QLabel("Log")
        log_header.setStyleSheet(self.theme.get_label_style('heading'))
        log_layout.addWidget(log_header)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setStyleSheet(self.theme.get_log_style())
        self.log_area.textChanged.connect(self.limit_log_size)
        log_layout.addWidget(self.log_area)

        splitter.addWidget(log_frame)

        # Translation preview
        preview_frame = QFrame()
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(0, 0, 0, 0)

        preview_header = QLabel("Translation Preview")
        preview_header.setStyleSheet(self.theme.get_label_style('heading'))
        preview_layout.addWidget(preview_header)

        preview_row = QHBoxLayout()

        orig_col = QVBoxLayout()
        orig_label = QLabel("Original")
        orig_label.setStyleSheet(self.theme.get_label_style('caption'))
        orig_col.addWidget(orig_label)
        self.original_text = QTextEdit()
        self.original_text.setReadOnly(True)
        self.original_text.setStyleSheet(self.theme.get_log_style())
        orig_col.addWidget(self.original_text)
        preview_row.addLayout(orig_col)

        arrow_label = QLabel("→")
        arrow_label.setStyleSheet(f"color: {self.theme.colors['primary']}; font-size: 24px; font-weight: bold;")
        arrow_label.setAlignment(Qt.AlignCenter)
        arrow_label.setFixedWidth(40)
        preview_row.addWidget(arrow_label)

        trans_col = QVBoxLayout()
        trans_label = QLabel("Translated")
        trans_label.setStyleSheet(self.theme.get_label_style('caption'))
        trans_col.addWidget(trans_label)
        self.translated_text = QTextEdit()
        self.translated_text.setReadOnly(True)
        self.translated_text.setStyleSheet(self.theme.get_log_style())
        trans_col.addWidget(self.translated_text)
        preview_row.addLayout(trans_col)

        preview_layout.addLayout(preview_row)
        splitter.addWidget(preview_frame)

        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter, 1)

    # ── Settings Tab ──

    def _create_settings_tab(self):
        tab = QWidget()
        self.tab_widget.addTab(tab, "⚙️  Settings")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(SPACING_LG, SPACING_LG, SPACING_LG, SPACING_LG)
        layout.setSpacing(SPACING_MD)

        # ── Section 1: Sonarr Connection ──
        conn_group = QGroupBox("Sonarr Connection")
        conn_layout = QGridLayout(conn_group)
        conn_layout.setSpacing(SPACING_SM)

        conn_layout.addWidget(QLabel('URL:'), 0, 0)
        self.sonarr_url = QLineEdit('http://localhost:8989')
        self.sonarr_url.editingFinished.connect(self.save_config)
        conn_layout.addWidget(self.sonarr_url, 0, 1)

        conn_layout.addWidget(QLabel('API Key:'), 1, 0)
        self.sonarr_api_key = QLineEdit()
        self.sonarr_api_key.setEchoMode(QLineEdit.Password)
        self.sonarr_api_key.editingFinished.connect(self.save_config)
        conn_layout.addWidget(self.sonarr_api_key, 1, 1)

        btn_row = QHBoxLayout()
        self.test_btn = QPushButton('Test Connection')
        self.test_btn.setStyleSheet(self.theme.get_button_style('primary'))
        self.test_btn.setFixedHeight(36)
        self.test_btn.clicked.connect(self.test_sonarr_connection)
        btn_row.addWidget(self.test_btn)

        self.load_series_btn = QPushButton('Load Series')
        self.load_series_btn.setStyleSheet(self.theme.get_button_style('success'))
        self.load_series_btn.setFixedHeight(36)
        self.load_series_btn.clicked.connect(self.load_series)
        self.load_series_btn.setEnabled(False)
        btn_row.addWidget(self.load_series_btn)

        self.connection_status = QLabel('Not connected')
        self.connection_status.setStyleSheet(self.theme.get_status_badge_style('disconnected'))
        btn_row.addWidget(self.connection_status)
        btn_row.addStretch()

        conn_layout.addLayout(btn_row, 2, 0, 1, 2)
        layout.addWidget(conn_group)

        # ── Section 2: Translation Engine ──
        engine_group = QGroupBox("Translation Engine")
        engine_layout = QGridLayout(engine_group)
        engine_layout.setSpacing(SPACING_SM)

        engine_layout.addWidget(QLabel('API:'), 0, 0)
        self.api_selector = QComboBox()
        self.api_selector.addItems(['Ollama', 'GPT', 'DeepL', 'Gemini', 'Google'])
        self.api_selector.setCurrentText('Ollama')
        self.api_selector.currentTextChanged.connect(self.save_config)
        self.api_selector.currentTextChanged.connect(self._on_api_selector_changed)
        engine_layout.addWidget(self.api_selector, 0, 1)

        engine_layout.addWidget(QLabel('Source Language:'), 4, 0)
        self.source_lang_combo = QComboBox()
        self.source_lang_combo.addItem('Auto-detect', 'auto')
        for code, label in [('en', 'English'), ('ja', 'Japanese'), ('es', 'Spanish'),
                             ('fr', 'French'), ('de', 'German'), ('it', 'Italian'),
                             ('pt', 'Portuguese'), ('ko', 'Korean'), ('zh', 'Chinese')]:
            self.source_lang_combo.addItem(label, code)
        self.source_lang_combo.setToolTip('Idioma da legenda de entrada. "Auto-detect" detecta automaticamente.')
        self.source_lang_combo.currentIndexChanged.connect(self.save_config)
        engine_layout.addWidget(self.source_lang_combo, 4, 1)

        engine_layout.addWidget(QLabel('Target Language:'), 5, 0)
        self.target_lang_combo = QComboBox()
        for code, label in [('pt-BR', 'Portuguese BR'), ('pt-PT', 'Portuguese PT'),
                             ('es', 'Spanish'), ('fr', 'French'), ('de', 'German'),
                             ('it', 'Italian'), ('en', 'English'), ('ja', 'Japanese')]:
            self.target_lang_combo.addItem(label, code)
        self.target_lang_combo.setCurrentIndex(0)
        self.target_lang_combo.setToolTip('Idioma de saída da tradução.')
        self.target_lang_combo.currentIndexChanged.connect(self.save_config)
        engine_layout.addWidget(self.target_lang_combo, 5, 1)

        engine_layout.addWidget(QLabel('Ollama URL:'), 1, 0)
        self.ollama_url = QLineEdit('http://localhost:11434')
        self.ollama_url.setToolTip('URL do Ollama. Geralmente http://localhost:11434')
        self.ollama_url.textChanged.connect(self.auto_test_api)
        self.ollama_url.editingFinished.connect(self.save_config)
        engine_layout.addWidget(self.ollama_url, 1, 1)

        engine_layout.addWidget(QLabel('Model:'), 2, 0)
        model_row = QHBoxLayout()

        self.ollama_model = QLineEdit()
        self.ollama_model.setVisible(False)

        self.ollama_model_combo = QComboBox()
        self.ollama_model_combo.setEditable(True)
        self.ollama_model_combo.setToolTip('Selecione um modelo instalado no Ollama ou digite o nome manualmente.')
        self.ollama_model_combo.currentIndexChanged.connect(self.on_model_selection_changed)
        self.ollama_model_combo.currentTextChanged.connect(self.on_model_text_changed)
        model_row.addWidget(self.ollama_model_combo, 1)

        self.refresh_models_btn = QPushButton('Refresh')
        self.refresh_models_btn.setFixedWidth(80)
        self.refresh_models_btn.setFixedHeight(32)
        self.refresh_models_btn.setStyleSheet(self.theme.get_button_style('ghost'))
        self.refresh_models_btn.clicked.connect(self.refresh_ollama_models)
        model_row.addWidget(self.refresh_models_btn)

        self.download_model_btn = QPushButton('Download')
        self.download_model_btn.setFixedWidth(90)
        self.download_model_btn.setFixedHeight(32)
        self.download_model_btn.setStyleSheet(self.theme.get_button_style('secondary'))
        self.download_model_btn.clicked.connect(self.download_selected_model)
        model_row.addWidget(self.download_model_btn)

        engine_layout.addLayout(model_row, 2, 1)

        # Hardware info badge
        self.hardware_info_label = QLabel('Detecting hardware...')
        self.hardware_info_label.setStyleSheet(self.theme.get_status_badge_style('info'))
        engine_layout.addWidget(self.hardware_info_label, 3, 0, 1, 2)

        layout.addWidget(engine_group)

        # ── Section 2b: External API Keys ──
        self.api_keys_group = QGroupBox("External API Key")
        api_keys_layout = QGridLayout(self.api_keys_group)
        api_keys_layout.setSpacing(SPACING_SM)

        self.deepl_key_label = QLabel('DeepL API Key:')
        self.deepl_key_input = QLineEdit()
        self.deepl_key_input.setEchoMode(QLineEdit.Password)
        self.deepl_key_input.setPlaceholderText('Sua chave DeepL (ex: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:fx)')
        self.deepl_key_input.editingFinished.connect(self.save_config)
        api_keys_layout.addWidget(self.deepl_key_label, 0, 0)
        api_keys_layout.addWidget(self.deepl_key_input, 0, 1)

        self.gpt_key_label = QLabel('OpenAI API Key:')
        self.gpt_key_input = QLineEdit()
        self.gpt_key_input.setEchoMode(QLineEdit.Password)
        self.gpt_key_input.setPlaceholderText('Sua chave OpenAI (ex: sk-...)')
        self.gpt_key_input.editingFinished.connect(self.save_config)
        api_keys_layout.addWidget(self.gpt_key_label, 1, 0)
        api_keys_layout.addWidget(self.gpt_key_input, 1, 1)

        self.gemini_key_label = QLabel('Gemini API Key:')
        self.gemini_key_input = QLineEdit()
        self.gemini_key_input.setEchoMode(QLineEdit.Password)
        self.gemini_key_input.setPlaceholderText('Sua chave Gemini (ex: AIza...)')
        self.gemini_key_input.editingFinished.connect(self.save_config)
        api_keys_layout.addWidget(self.gemini_key_label, 2, 0)
        api_keys_layout.addWidget(self.gemini_key_input, 2, 1)

        layout.addWidget(self.api_keys_group)
        self.api_keys_group.setVisible(False)

        # ── Section 3: Performance ──
        perf_group = QGroupBox("Performance")
        perf_layout = QGridLayout(perf_group)
        perf_layout.setSpacing(SPACING_SM)

        perf_layout.addWidget(QLabel('Parallelism:'), 0, 0)
        self.parallelism = QSpinBox()
        self.parallelism.setRange(1, 2)
        self.parallelism.setValue(1)
        self.parallelism.setToolTip('1 = one request at a time. 2 = two in parallel (faster, higher GPU usage).')
        self.parallelism.valueChanged.connect(self.save_config)
        perf_layout.addWidget(self.parallelism, 0, 1)

        perf_layout.addWidget(QLabel('Context Window:'), 1, 0)
        self.context_window_spin = QSpinBox()
        self.context_window_spin.setRange(0, 10)
        self.context_window_spin.setValue(3)
        self.context_window_spin.setToolTip('Number of previous lines sent as context for each translation (0 = disabled).')
        self.context_window_spin.valueChanged.connect(self.save_config)
        perf_layout.addWidget(self.context_window_spin, 1, 1)

        perf_layout.addWidget(QLabel('Batch Size:'), 2, 0)
        batch_row = QHBoxLayout()
        self.batch_size_slider = QSlider(Qt.Horizontal)
        self.batch_size_slider.setRange(0, 4)
        self.batch_size_slider.setValue(0)
        self.batch_size_slider.setTickPosition(QSlider.TicksBelow)
        self.batch_size_slider.setTickInterval(1)
        self.batch_size_slider.valueChanged.connect(self._on_batch_slider_changed)
        batch_row.addWidget(self.batch_size_slider)

        self.batch_size_label = QLabel('Off')
        self.batch_size_label.setFixedWidth(60)
        self.batch_size_label.setAlignment(Qt.AlignCenter)
        self.batch_size_label.setStyleSheet(self.theme.get_label_style('mono'))
        batch_row.addWidget(self.batch_size_label)
        perf_layout.addLayout(batch_row, 2, 1)

        layout.addWidget(perf_group)

        # ── Section 4: Advanced (collapsible) ──
        advanced_section = CollapsibleSection("Advanced Settings", collapsed=True)

        # Skip existing
        self.skip_existing_cb = QCheckBox('Skip existing pt-BR subtitle files')
        self.skip_existing_cb.setChecked(True)
        self.skip_existing_cb.stateChanged.connect(self.save_config)
        advanced_section.add_widget(self.skip_existing_cb)

        # Webhook toggle
        self.enable_webhook_cb = QCheckBox('Enable Webhook server on startup')
        self.enable_webhook_cb.setChecked(False)
        self.enable_webhook_cb.stateChanged.connect(self.save_config)
        advanced_section.add_widget(self.enable_webhook_cb)

        # Tray notification
        self.notify_done_cb = QCheckBox('Notify when translation is done (system tray)')
        self.notify_done_cb.setChecked(True)
        self.notify_done_cb.stateChanged.connect(self.save_config)
        advanced_section.add_widget(self.notify_done_cb)

        # Review before save
        self.review_before_save_cb = QCheckBox('Review translations before saving')
        self.review_before_save_cb.setChecked(False)
        self.review_before_save_cb.stateChanged.connect(self.save_config)
        advanced_section.add_widget(self.review_before_save_cb)

        # Feature flags heading
        flags_label = QLabel("Feature Flags")
        flags_label.setStyleSheet(self.theme.get_label_style('heading'))
        advanced_section.add_widget(flags_label)

        self.enable_contextual_prompt_cb = QCheckBox('Contextual Prompt (include surrounding lines in prompt)')
        self.enable_contextual_prompt_cb.setChecked(True)
        self.enable_contextual_prompt_cb.stateChanged.connect(self.save_config)
        advanced_section.add_widget(self.enable_contextual_prompt_cb)

        self.enable_fewshot_cb = QCheckBox('Few-shot Examples (genre-specific translation examples)')
        self.enable_fewshot_cb.setChecked(True)
        self.enable_fewshot_cb.stateChanged.connect(self.save_config)
        advanced_section.add_widget(self.enable_fewshot_cb)

        self.enable_auto_glossary_cb = QCheckBox('Auto Glossary (learn character names automatically)')
        self.enable_auto_glossary_cb.setChecked(True)
        self.enable_auto_glossary_cb.stateChanged.connect(self.save_config)
        advanced_section.add_widget(self.enable_auto_glossary_cb)

        glossary_model_row = QHBoxLayout()
        glossary_model_row.addWidget(QLabel('Glossary Model (pre-scan):'))
        self.glossary_model_combo = QComboBox()
        self.glossary_model_combo.setEditable(True)
        self.glossary_model_combo.setToolTip('Modelo leve usado para extrair nomes/termos antes da tradução (uma vez por série).')
        self.glossary_model_combo.currentTextChanged.connect(self.save_config)
        glossary_model_row.addWidget(self.glossary_model_combo, 1)
        advanced_section.add_layout(glossary_model_row)

        # Ollama Performance
        perf_label = QLabel("Ollama Performance")
        perf_label.setStyleSheet(self.theme.get_label_style('heading'))
        advanced_section.add_widget(perf_label)

        ctx_row = QHBoxLayout()
        ctx_row.addWidget(QLabel('Context Window (num_ctx):'))
        self.num_ctx_combo = QComboBox()
        self.num_ctx_combo.addItems(['1024', '2048', '4096', '8192'])
        self.num_ctx_combo.setCurrentText('2048')
        self.num_ctx_combo.setToolTip(
            'Tamanho da janela de contexto do Ollama. Valores menores liberam VRAM para mais layers na GPU.\n'
            '2048 é ideal para legendas. Aumente só se tiver VRAM sobrando.')
        self.num_ctx_combo.currentTextChanged.connect(self.save_config)
        ctx_row.addWidget(self.num_ctx_combo)
        advanced_section.add_layout(ctx_row)

        thread_row = QHBoxLayout()
        thread_row.addWidget(QLabel('CPU Threads (num_thread):'))
        self.num_thread_spin = QSpinBox()
        self.num_thread_spin.setRange(0, 32)
        self.num_thread_spin.setValue(0)
        self.num_thread_spin.setToolTip(
            'Threads CPU para layers offloaded. 0 = auto (Ollama decide).\n'
            'Para modelos grandes que não cabem na VRAM, ajustar para o número de cores físicos pode ajudar.')
        self.num_thread_spin.valueChanged.connect(self.save_config)
        thread_row.addWidget(self.num_thread_spin)
        advanced_section.add_layout(thread_row)

        # Import GGUF
        import_row = QHBoxLayout()
        self.import_gguf_btn = QPushButton('Import GGUF')
        self.import_gguf_btn.setFixedHeight(32)
        self.import_gguf_btn.setStyleSheet(self.theme.get_button_style('secondary'))
        self.import_gguf_btn.setToolTip('Create an Ollama model from a local .gguf file')
        self.import_gguf_btn.clicked.connect(self.import_local_gguf)
        import_row.addWidget(self.import_gguf_btn)
        import_row.addStretch()
        advanced_section.add_layout(import_row)

        layout.addWidget(advanced_section)

        layout.addStretch()
        scroll.setWidget(content)

        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(scroll)

    # ── API selector visibility ──

    # ── Local Files Tab ──

    def _create_local_files_tab(self):
        tab = QWidget()
        self.tab_widget.addTab(tab, "📁  Local Files")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── Source selection ──
        source_group = QGroupBox("Source")
        source_layout = QVBoxLayout(source_group)
        source_layout.setSpacing(6)

        mode_row = QHBoxLayout()
        self._local_folder_radio = QRadioButton("Folder (scan recursively)")
        self._local_files_radio = QRadioButton("Individual files")
        self._local_folder_radio.setChecked(True)
        self._local_mode_group = QButtonGroup(self)
        self._local_mode_group.addButton(self._local_folder_radio, 0)
        self._local_mode_group.addButton(self._local_files_radio, 1)
        self._local_mode_group.buttonClicked.connect(self._on_local_mode_changed)
        mode_row.addWidget(self._local_folder_radio)
        mode_row.addWidget(self._local_files_radio)
        mode_row.addStretch()
        source_layout.addLayout(mode_row)

        # Folder mode widgets
        self._local_folder_widget = QWidget()
        folder_row = QHBoxLayout(self._local_folder_widget)
        folder_row.setContentsMargins(0, 0, 0, 0)
        self._local_folder_path = QLineEdit()
        self._local_folder_path.setPlaceholderText("Select a folder containing video or subtitle files...")
        self._local_folder_path.setReadOnly(True)
        folder_row.addWidget(self._local_folder_path, 1)
        self._local_browse_btn = QPushButton("Browse")
        self._local_browse_btn.setFixedWidth(90)
        self._local_browse_btn.setStyleSheet(self.theme.get_button_style('secondary'))
        self._local_browse_btn.clicked.connect(self._browse_local_folder)
        folder_row.addWidget(self._local_browse_btn)
        self._local_scan_btn = QPushButton("Scan")
        self._local_scan_btn.setFixedWidth(70)
        self._local_scan_btn.setStyleSheet(self.theme.get_button_style('primary'))
        self._local_scan_btn.clicked.connect(self._scan_local_folder)
        folder_row.addWidget(self._local_scan_btn)
        source_layout.addWidget(self._local_folder_widget)

        # Files mode widgets
        self._local_files_widget = QWidget()
        self._local_files_widget.setVisible(False)
        files_row = QHBoxLayout(self._local_files_widget)
        files_row.setContentsMargins(0, 0, 0, 0)
        self._local_add_files_btn = QPushButton("Add Files")
        self._local_add_files_btn.setStyleSheet(self.theme.get_button_style('primary'))
        self._local_add_files_btn.clicked.connect(self._add_local_files)
        files_row.addWidget(self._local_add_files_btn)
        self._local_remove_btn = QPushButton("Remove Selected")
        self._local_remove_btn.setStyleSheet(self.theme.get_button_style('ghost'))
        self._local_remove_btn.clicked.connect(self._remove_local_files)
        files_row.addWidget(self._local_remove_btn)
        files_row.addStretch()
        source_layout.addWidget(self._local_files_widget)

        layout.addWidget(source_group)

        # ── File list ──
        self._local_file_tree = QTreeWidget()
        self._local_file_tree.setColumnCount(4)
        self._local_file_tree.setHeaderLabels(["File", "Format", "Size", "Status"])
        self._local_file_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._local_file_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._local_file_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._local_file_tree.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._local_file_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self._local_file_tree.setRootIsDecorated(False)
        layout.addWidget(self._local_file_tree, 1)

        # ── Action buttons ──
        btn_row = QHBoxLayout()
        self._local_translate_all_btn = QPushButton("Translate All")
        self._local_translate_all_btn.setFixedHeight(40)
        self._local_translate_all_btn.setStyleSheet(self.theme.get_button_style('success'))
        self._local_translate_all_btn.clicked.connect(self._local_translate_all)
        btn_row.addWidget(self._local_translate_all_btn)
        self._local_translate_sel_btn = QPushButton("Translate Selected")
        self._local_translate_sel_btn.setFixedHeight(40)
        self._local_translate_sel_btn.setStyleSheet(self.theme.get_button_style('primary'))
        self._local_translate_sel_btn.clicked.connect(self._local_translate_selected)
        btn_row.addWidget(self._local_translate_sel_btn)
        self._local_clear_btn = QPushButton("Clear List")
        self._local_clear_btn.setFixedHeight(40)
        self._local_clear_btn.setStyleSheet(self.theme.get_button_style('ghost'))
        self._local_clear_btn.clicked.connect(self._local_file_tree.clear)
        btn_row.addWidget(self._local_clear_btn)
        layout.addLayout(btn_row)

        # Internal state
        self._local_file_paths: list = []

    def _on_local_mode_changed(self):
        is_folder = self._local_folder_radio.isChecked()
        self._local_folder_widget.setVisible(is_folder)
        self._local_files_widget.setVisible(not is_folder)

    def _browse_local_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder", "")
        if folder:
            self._local_folder_path.setText(folder)
            self._scan_local_folder()

    def _scan_local_folder(self):
        folder = self._local_folder_path.text().strip()
        if not folder:
            QMessageBox.warning(self, "No folder", "Please select a folder first.")
            return
        self._local_file_tree.clear()
        self._local_file_paths = []
        self._local_scan_btn.setEnabled(False)
        self._local_scan_btn.setText("Scanning...")

        self._local_scanner_worker = _LocalScanWorker(folder)
        self._local_scanner_worker.finished.connect(self._on_local_scan_done)
        self._local_scanner_worker.start()

    def _on_local_scan_done(self, paths):
        self._local_scan_btn.setEnabled(True)
        self._local_scan_btn.setText("Scan")
        self._populate_local_tree(paths)

    def _add_local_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Files", "",
            "Video & Subtitle Files (*.mkv *.mp4 *.avi *.mov *.wmv *.srt *.ass *.vtt);;All Files (*)"
        )
        if paths:
            existing = set(self._local_file_paths)
            new_paths = [p for p in paths if p not in existing]
            self._populate_local_tree(self._local_file_paths + new_paths)

    def _remove_local_files(self):
        selected = self._local_file_tree.selectedItems()
        for item in selected:
            path = item.data(0, Qt.UserRole)
            if path in self._local_file_paths:
                self._local_file_paths.remove(path)
            idx = self._local_file_tree.indexOfTopLevelItem(item)
            if idx >= 0:
                self._local_file_tree.takeTopLevelItem(idx)

    def _populate_local_tree(self, paths):
        self._local_file_tree.clear()
        self._local_file_paths = list(paths)
        target_lang = self.target_lang_combo.currentData() if hasattr(self, 'target_lang_combo') else 'pt-BR'
        for path_str in paths:
            p = Path(path_str)
            ext = p.suffix.lower().lstrip('.')
            size_mb = f"{p.stat().st_size / 1_048_576:.1f} MB" if p.exists() else "?"
            translated_ext = p.suffix.lower()
            translated_name = p.with_name(p.stem + f'.{target_lang}' + translated_ext)
            status = "✓ Translated" if translated_name.exists() else "Pending"
            item = QTreeWidgetItem([p.name, ext.upper(), size_mb, status])
            item.setData(0, Qt.UserRole, path_str)
            self._local_file_tree.addTopLevelItem(item)

    def _local_translate_all(self):
        if not self._local_file_paths:
            QMessageBox.information(self, "No files", "Add files or scan a folder first.")
            return
        self._start_local_processing(self._local_file_paths)

    def _local_translate_selected(self):
        selected = self._local_file_tree.selectedItems()
        if not selected:
            QMessageBox.warning(self, "No selection", "Select files in the list first.")
            return
        paths = [item.data(0, Qt.UserRole) for item in selected if item.data(0, Qt.UserRole)]
        self._start_local_processing(paths)

    def _start_local_processing(self, file_paths):
        if not file_paths:
            return
        batch_val = self._BATCH_MAP[self.batch_size_slider.value()]
        keys = {
            'ollama_url': self.ollama_url.text(),
            'ollama_model': self._get_effective_ollama_model(),
            'parallelism': self.parallelism.value(),
            'skip_existing': self.skip_existing_cb.isChecked(),
            'use_micro_batch_2': batch_val >= 2,
            'use_batch_prompt': batch_val >= 4,
            'srt_batch_size': batch_val if batch_val >= 4 else 0,
            'ass_batch_size': batch_val if batch_val >= 2 else 1,
            'num_ctx': int(self.num_ctx_combo.currentText()),
            'num_thread': self.num_thread_spin.value(),
            'enable_contextual_prompt': self.enable_contextual_prompt_cb.isChecked(),
            'enable_fewshot': self.enable_fewshot_cb.isChecked(),
            'enable_auto_glossary': self.enable_auto_glossary_cb.isChecked(),
            'context_window_size': self.context_window_spin.value(),
            'glossary_model': self.glossary_model_combo.currentText().strip() if hasattr(self, 'glossary_model_combo') else '',
            'deepl': self.deepl_key_input.text().strip() if hasattr(self, 'deepl_key_input') else '',
            'gpt': self.gpt_key_input.text().strip() if hasattr(self, 'gpt_key_input') else '',
            'gemini': self.gemini_key_input.text().strip() if hasattr(self, 'gemini_key_input') else '',
            'source_lang': self.source_lang_combo.currentData() if hasattr(self, 'source_lang_combo') else 'auto',
            'target_lang': self.target_lang_combo.currentData() if hasattr(self, 'target_lang_combo') else 'pt-BR',
        }

        # Usar hash do primeiro arquivo como series_id fictício para o TranslationTracker
        import hashlib
        fake_series_id = hashlib.md5(str(file_paths[0]).encode()).hexdigest()[:8]
        fake_series = {
            'id': fake_series_id,
            'title': 'Local Files',
            'path': str(Path(file_paths[0]).parent),
            'episodeCount': len(file_paths),
            'genres': [],
        }

        import time
        self.start_time = time.time()
        self.processing_queue = [fake_series]

        api = self.api_selector.currentText() if hasattr(self, 'api_selector') else 'Ollama'
        self.worker = ProcessingWorker([fake_series], keys, api, specific_files=file_paths)
        self.worker.progress.connect(self.update_processing_progress)
        self.worker.log_update.connect(self.log_area.append)
        self.worker.translation_update.connect(self.update_translation_display)
        self.worker.finished.connect(self.processing_finished)
        self.worker.start()

        self.tab_widget.setCurrentIndex(1)
        self.stop_processing_btn.setEnabled(True)

    def _on_api_selector_changed(self, api_name):
        """Show/hide external API key fields based on selected API."""
        external_apis = {'GPT', 'DeepL', 'Gemini'}
        self.api_keys_group.setVisible(api_name in external_apis)

        # Show only the relevant key field
        self.deepl_key_label.setVisible(api_name == 'DeepL')
        self.deepl_key_input.setVisible(api_name == 'DeepL')
        self.gpt_key_label.setVisible(api_name == 'GPT')
        self.gpt_key_input.setVisible(api_name == 'GPT')
        self.gemini_key_label.setVisible(api_name == 'Gemini')
        self.gemini_key_input.setVisible(api_name == 'Gemini')

    # ── Batch slider helper ──

    _BATCH_MAP = [0, 2, 4, 8, 12]

    def _on_batch_slider_changed(self, index):
        val = self._BATCH_MAP[index]
        self.batch_size_label.setText('Off' if val == 0 else f'{val} lines')
        self.save_config()

    # ── Webhook ──

    def _start_webhook(self):
        try:
            self.status_bar.showMessage('Webhook disabled by default')
        except Exception:
            pass

    # ── Sonarr Connection ──

    def test_sonarr_connection(self):
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
                self.connection_status.setText('Connected')
                self.connection_status.setStyleSheet(self.theme.get_status_badge_style('connected'))
                self.sonarr_badge.setText('Connected')
                self.sonarr_badge.setStyleSheet(self.theme.get_status_badge_style('connected'))
                self.load_series_btn.setEnabled(True)
                self.save_config()
            else:
                self.connection_status.setText('Failed')
                self.connection_status.setStyleSheet(self.theme.get_status_badge_style('error'))
                self.sonarr_badge.setText('Disconnected')
                self.sonarr_badge.setStyleSheet(self.theme.get_status_badge_style('disconnected'))
                self.sonarr_client = None
        except Exception as e:
            self.connection_status.setText(f'Error')
            self.connection_status.setStyleSheet(self.theme.get_status_badge_style('error'))
            self.connection_status.setToolTip(str(e))
            self.sonarr_client = None
        self.test_btn.setEnabled(True)
        self.test_btn.setText('Test Connection')

    def load_series(self):
        if not self.sonarr_client:
            return
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
            for series in series_data:
                episodes = self.sonarr_client.get_episodes(series['id'])
                episode_files = self.sonarr_client.get_episode_files(series['id'])
                file_lookup = {f['id']: f for f in episode_files}
                eps_with_files = []
                for ep in episodes:
                    if ep.get('hasFile') and ep.get('episodeFileId'):
                        fd = file_lookup.get(ep['episodeFileId'])
                        if fd:
                            eps_with_files.append({'id': ep['id'], 'video_path': fd.get('path')})
                series['translation_stats'] = self.translation_tracker.get_series_stats(series['id'], eps_with_files)

            self.series_cache = series_data
            self.cache_timestamp = current_time
            self.populate_series_grid(series_data)
            self.status_bar.showMessage(f'Loaded {len(series_data)} series')
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load series: {str(e)}")
        self.load_series_btn.setEnabled(True)
        self.load_series_btn.setText('Load Series')

    # ── Log helpers ──

    def limit_log_size(self):
        if self.log_area.document().lineCount() > 1000:
            cursor = self.log_area.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            for _ in range(500):
                cursor.movePosition(cursor.MoveOperation.Down)
            cursor.movePosition(cursor.MoveOperation.Start, cursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.insertText("[Log truncated]\n")

    def add_colored_log(self, level, message):
        if not hasattr(self, 'log_area'):
            return
        colors = {
            'info': self.theme.colors['info'],
            'warning': self.theme.colors['warning'],
            'error': self.theme.colors['error'],
            'success': self.theme.colors['success']
        }
        color = colors.get(level.lower(), self.theme.colors['text'])
        ts = QTime.currentTime().toString("hh:mm:ss")
        html = (f'<span style="color:{self.theme.colors["text_secondary"]}">[{ts}]</span> '
                f'<span style="color:{color};font-weight:600">{level.upper()}:</span> '
                f'<span style="color:{self.theme.colors["text"]}">{message}</span>')
        self.log_area.append(html)
        self.log_area.verticalScrollBar().setValue(self.log_area.verticalScrollBar().maximum())

    # ── API / cache ──

    def auto_test_api(self):
        if not hasattr(self, 'auto_test_timer'):
            self.auto_test_timer = QTimer()
            self.auto_test_timer.setSingleShot(True)
            self.auto_test_timer.timeout.connect(self.test_current_api)
        self.auto_test_timer.start(1000)

    def update_cache_stats(self):
        try:
            from .translation_cache import TranslationCache
            cache = TranslationCache()
            stats = cache.get_stats()
            total = stats.get('total_entries', 0)
            hits = stats.get('total_hits', 0)
            if total > 0:
                reuse = (hits / total) * 100 if total > 0 else 0
                self.cache_stats_label.setText(f'Cache: {total:,} entries · {reuse:.0f}% avg hits')
                self.cache_stats_label.setStyleSheet(
                    f"color: {self.theme.colors['success']}; font-size: 11px;")
            else:
                self.cache_stats_label.setText('Cache: Empty')
                self.cache_stats_label.setStyleSheet(self.theme.get_label_style('caption'))
        except Exception:
            self.cache_stats_label.setText('Cache: Error')
            self.cache_stats_label.setStyleSheet(f"color: {self.theme.colors['error']}; font-size: 11px;")

    def clear_translation_cache(self):
        reply = QMessageBox.question(self, 'Clear Cache',
                                     'Clear all cached translations?',
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                from .translation_cache import TranslationCache
                cache = TranslationCache()
                cache.clear_cache()
                self.update_cache_stats()
                QMessageBox.information(self, 'Done', 'Cache cleared.')
            except Exception as e:
                QMessageBox.critical(self, 'Error', f'Failed: {str(e)}')

    def test_current_api(self):
        self.api_status_label.setText('Testing...')
        self.api_status_label.setStyleSheet(f"color: {self.theme.colors['warning']}; font-size: 13px;")
        self.test_api_btn.setEnabled(False)
        try:
            import time
            url = self.ollama_url.text() or 'http://localhost:11434'
            model = self._get_effective_ollama_model()
            start = time.time()
            resp = requests.get(f"{url}/api/tags", timeout=5)
            latency = int((time.time() - start) * 1000)
            if resp.status_code == 200:
                models_data = resp.json().get('models', [])
                if self.ollama_model_combo.count() <= 1:
                    self.refresh_ollama_models()
                names = [m.get('name', '') for m in models_data]
                found = any(model in n for n in names)
                if found:
                    self.api_status_label.setText(f'Ollama: OK ({latency}ms)')
                    self.api_status_label.setStyleSheet(f"color: {self.theme.colors['success']}; font-size: 13px; font-weight: 600;")
                    self.model_info_label.setText(f'Model: {model} ✓')
                    self.model_info_label.setStyleSheet(f"color: {self.theme.colors['success']}; font-size: 11px;")
                else:
                    self.api_status_label.setText(f'Ollama: OK · Model missing')
                    self.api_status_label.setStyleSheet(f"color: {self.theme.colors['warning']}; font-size: 13px; font-weight: 600;")
                    self.model_info_label.setText(f'Model: {model} ✗ (use Download)')
                    self.model_info_label.setStyleSheet(f"color: {self.theme.colors['warning']}; font-size: 11px;")
            else:
                raise Exception("Connection failed")
        except requests.exceptions.ConnectionError:
            self.api_status_label.setText('Ollama: Not running')
            self.api_status_label.setStyleSheet(f"color: {self.theme.colors['error']}; font-size: 13px; font-weight: 600;")
            self.model_info_label.setText('Model: —')
            self.model_info_label.setStyleSheet(self.theme.get_label_style('caption'))
        except Exception as e:
            self.api_status_label.setText(f'Ollama: Error')
            self.api_status_label.setStyleSheet(f"color: {self.theme.colors['error']}; font-size: 13px; font-weight: 600;")
            self.api_status_label.setToolTip(str(e))
        finally:
            self.test_api_btn.setEnabled(True)
            self.update_cache_stats()

    # ── Series grid ──

    def refresh_series_list(self):
        if not self.sonarr_client:
            QMessageBox.warning(self, "Warning", "Connect to Sonarr first in Settings")
            return
        self.series_cache = {}
        self.cache_timestamp = None
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText('Refreshing...')
        self.status_bar.showMessage('Refreshing...')
        try:
            series_data = self.sonarr_client.get_series_with_files()
            for series in series_data:
                episodes = self.sonarr_client.get_episodes(series['id'])
                episode_files = self.sonarr_client.get_episode_files(series['id'])
                file_lookup = {f['id']: f for f in episode_files}
                eps_with_files = []
                for ep in episodes:
                    if ep.get('hasFile') and ep.get('episodeFileId'):
                        fd = file_lookup.get(ep['episodeFileId'])
                        if fd:
                            eps_with_files.append({'id': ep['id'], 'video_path': fd.get('path')})
                series['translation_stats'] = self.translation_tracker.get_series_stats(series['id'], eps_with_files)
            import time
            self.series_cache = series_data
            self.cache_timestamp = time.time()
            self.populate_series_grid(series_data)
            self.status_bar.showMessage(f'Refreshed: {len(series_data)} series')
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed: {str(e)}")
            self.status_bar.showMessage('Refresh failed')
        finally:
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText('Refresh')

    def populate_series_grid(self, series_data):
        self.original_series_data = series_data
        self.anime_grid.populate_series(series_data)
        self.series_count_label.setText(f'{len(series_data)} series')
        self.tab_widget.setCurrentIndex(0)

    def filter_series(self, search_text):
        if not hasattr(self, 'original_series_data'):
            return
        if not search_text.strip():
            filtered = self.original_series_data
            self.filter_indicator.hide()
        else:
            q = search_text.lower()
            filtered = []
            for s in self.original_series_data:
                if q in s['title'].lower():
                    filtered.append(s); continue
                if s.get('year') and q in str(s['year']):
                    filtered.append(s); continue
                if s.get('network') and q in s['network'].lower():
                    filtered.append(s); continue
                if s.get('genres') and any(q in g.lower() for g in s['genres']):
                    filtered.append(s)
            self.filter_indicator.setText(f"Filter active: {len(filtered)} results")
            self.filter_indicator.show()

        self.anime_grid.populate_series(filtered)
        if search_text.strip():
            self.series_count_label.setText(f'{len(filtered)} / {len(self.original_series_data)}')
        else:
            self.series_count_label.setText(f'{len(filtered)} series')

    # ── Episode dialog ──

    def show_episode_details(self, series_data):
        if not self.sonarr_client:
            QMessageBox.warning(self, "Error", "Not connected to Sonarr")
            return
        dialog = EpisodeDetailsDialog(series_data, self.sonarr_client, self.translation_tracker, self)
        dialog.exec()

    def quick_translate_series(self, series_data):
        reply = QMessageBox.question(
            self, "Translate Series",
            f"Translate all episodes of {series_data['title']}?",
            QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.start_single_series_processing([series_data])

    def start_episode_processing(self, episodes, series_data, track_selections=None):
        file_paths = []
        track_map = {}  # file_path -> track_id
        for ep_data in episodes:
            fi = ep_data.get('file_data')
            if fi and fi.get('path'):
                fp = fi['path']
                file_paths.append(fp)
                ep_id = ep_data.get('episode', {}).get('id')
                if track_selections and ep_id in track_selections:
                    tid = track_selections[ep_id]
                    if tid is not None:
                        track_map[fp] = tid
        if not file_paths:
            QMessageBox.warning(self, "Warning", "No valid episode files found")
            return
        temp = {
            'title': series_data['title'],
            'path': str(Path(file_paths[0]).parent),
            'files': file_paths
        }
        self.start_single_series_processing([temp], specific_files=file_paths, track_map=track_map)

    # ── Processing ──

    def start_single_series_processing(self, series_list, specific_files=None, track_map=None):
        batch_val = self._BATCH_MAP[self.batch_size_slider.value()]
        keys = {
            'ollama_url': self.ollama_url.text(),
            'ollama_model': self._get_effective_ollama_model(),
            'parallelism': self.parallelism.value(),
            'skip_existing': self.skip_existing_cb.isChecked(),
            'use_micro_batch_2': batch_val >= 2,
            'use_batch_prompt': batch_val >= 4,
            'srt_batch_size': batch_val if batch_val >= 4 else 0,
            'ass_batch_size': batch_val if batch_val >= 2 else 1,
            'num_ctx': int(self.num_ctx_combo.currentText()),
            'num_thread': self.num_thread_spin.value(),
            'enable_contextual_prompt': self.enable_contextual_prompt_cb.isChecked(),
            'enable_fewshot': self.enable_fewshot_cb.isChecked(),
            'enable_auto_glossary': self.enable_auto_glossary_cb.isChecked(),
            'context_window_size': self.context_window_spin.value(),
            'glossary_model': getattr(self, 'glossary_model_combo', None) and self.glossary_model_combo.currentText().strip() or self._get_effective_ollama_model(),
            # External API keys
            'deepl': self.deepl_key_input.text().strip() if hasattr(self, 'deepl_key_input') else '',
            'gpt': self.gpt_key_input.text().strip() if hasattr(self, 'gpt_key_input') else '',
            'gemini': self.gemini_key_input.text().strip() if hasattr(self, 'gemini_key_input') else '',
            # Language config
            'source_lang': self.source_lang_combo.currentData() if hasattr(self, 'source_lang_combo') else 'auto',
            'target_lang': self.target_lang_combo.currentData() if hasattr(self, 'target_lang_combo') else 'pt-BR',
            # Review mode
            'review_before_save': self.review_before_save_cb.isChecked() if hasattr(self, 'review_before_save_cb') else False,
        }

        self.processing_queue = series_list.copy()
        import time
        self.start_time = time.time()

        if series_list and len(series_list) == 1:
            sid = series_list[0].get('id')
            if sid:
                self.anime_grid.start_processing_series(sid)

        api = self.api_selector.currentText() if hasattr(self, 'api_selector') else 'Ollama'
        self.worker = ProcessingWorker(series_list, keys, api, specific_files, track_map=track_map)
        self.worker.progress.connect(self.update_processing_progress)
        self.worker.log_update.connect(self.log_area.append)
        self.worker.translation_update.connect(self.update_translation_display)
        self.worker.finished.connect(self.processing_finished)
        self.worker.review_ready.connect(self._on_review_ready)
        self._review_queue: list = []
        self.worker.start()

        self.tab_widget.setCurrentIndex(1)
        self.stop_processing_btn.setEnabled(True)

    def stop_processing(self):
        if self.worker:
            self.worker.stop()
            if hasattr(self.worker, 'processor') and self.worker.processor:
                self.worker.processor.stop_processing()
                if hasattr(self.worker.processor, 'translator'):
                    self.worker.processor.translator.stop_translation()
        self.processing_finished()

    def processing_finished(self):
        self.stop_processing_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.eta_label.setText('ETA: —')
        self.log_area.append("✅ Processing completed!")

        n_series = len(self.processing_queue) if self.processing_queue else 1
        self.processing_queue = []
        self.start_time = None

        if hasattr(self, 'worker') and self.worker and len(self.worker.selected_series) == 1:
            sid = self.worker.selected_series[0].get('id')
            if sid:
                self.anime_grid.finish_processing_series(sid)

        # Show review dialog if enabled
        if hasattr(self, 'review_before_save_cb') and self.review_before_save_cb.isChecked():
            if hasattr(self, '_review_queue') and self._review_queue:
                self._show_review_dialog()

        # System tray notification
        notify = True
        if hasattr(self, 'notify_done_cb'):
            notify = self.notify_done_cb.isChecked()
        if notify and self.tray_icon and self.tray_icon.isVisible():
            self.tray_icon.showMessage(
                "Tradução Concluída",
                f"{n_series} série(s) processada(s) com sucesso.",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    def _on_review_ready(self, output_path: str, lines: list):
        """Accumulate review items and show dialog when worker finishes."""
        self._review_queue.append({
            'output_path': output_path,
            'lines': lines,
            'total': 0,
            'index': len(self._review_queue),
        })

    def _show_review_dialog(self):
        if not self._review_queue:
            return
        total = len(self._review_queue)
        for entry in self._review_queue:
            entry['total'] = total

        dlg = TranslationReviewDialog(self._review_queue, parent=self)
        dlg.saved.connect(self._apply_review_save)
        dlg.skipped.connect(lambda p: self.log_area.append(f"⏭ Skipped review: {p}"))
        dlg.exec()
        self._review_queue = []

    def _apply_review_save(self, output_path: str, lines: list):
        """Write the reviewed (possibly edited) translation to disk."""
        try:
            from pathlib import Path as _Path
            p = _Path(output_path)
            ext = p.suffix.lower()
            if ext == '.srt':
                content_lines = []
                for i, (orig, trans) in enumerate(lines, 1):
                    content_lines.append(f"{i}\n00:00:00,000 --> 00:00:01,000\n{trans}\n")
                p.write_text("\n".join(content_lines), encoding='utf-8')
            else:
                p.write_text("\n".join(t for _, t in lines), encoding='utf-8')
            self.log_area.append(f"✅ Saved (reviewed): {p.name}")
        except Exception as e:
            self.log_area.append(f"❌ Error saving review: {e}")

    def update_translation_display(self, original, translated, api):
        self.original_text.append(f"[{api}] {original}")
        self.translated_text.append(f"[{api}] {translated}")

    def update_processing_progress(self, progress):
        self.progress_bar.setValue(int(progress))
        if self.start_time and progress > 0:
            import time
            elapsed = time.time() - self.start_time
            if progress > 5:
                remaining = elapsed * (100 / progress) - elapsed
                if remaining > 0:
                    if remaining < 60:
                        self.eta_label.setText(f"ETA: {int(remaining)}s")
                    elif remaining < 3600:
                        self.eta_label.setText(f"ETA: {int(remaining/60)}m {int(remaining%60)}s")
                    else:
                        self.eta_label.setText(f"ETA: {int(remaining/3600)}h {int((remaining%3600)/60)}m")
                else:
                    self.eta_label.setText("ETA: —")
            else:
                self.eta_label.setText("Calculating...")

        if hasattr(self, 'worker') and self.worker and len(self.worker.selected_series) == 1:
            sid = self.worker.selected_series[0].get('id')
            if sid:
                self.anime_grid.update_series_progress(sid, progress)

    # ── Config ──

    def load_config(self):
        if not os.path.exists(self.config_file):
            return
        try:
            with open(self.config_file, 'r') as f:
                config = json.load(f)

            self.sonarr_url.setText(config.get('sonarr_url', 'http://localhost:8989'))
            self.sonarr_api_key.setText(config.get('sonarr_api_key', ''))
            self.ollama_url.setText(config.get('ollama_url', 'http://localhost:11434'))
            self.ollama_model.setText(config.get('ollama_model', 'qwen2.5:32b-instruct-q4_K_M'))
            if hasattr(self, 'init_model_selector'):
                self.init_model_selector()
            self.parallelism.setValue(int(config.get('parallelism', 1)))
            self.skip_existing_cb.setChecked(bool(config.get('skip_existing', True)))
            self.enable_webhook_cb.setChecked(bool(config.get('enable_webhook', False)))
            if hasattr(self, 'notify_done_cb'):
                self.notify_done_cb.setChecked(bool(config.get('notify_done', True)))
            if hasattr(self, 'review_before_save_cb'):
                self.review_before_save_cb.setChecked(bool(config.get('review_before_save', False)))

            if hasattr(self, 'api_selector'):
                self.api_selector.setCurrentText(config.get('api', 'Ollama'))
                self._on_api_selector_changed(self.api_selector.currentText())

            # External API keys
            if hasattr(self, 'deepl_key_input'):
                self.deepl_key_input.setText(config.get('deepl_key', ''))
            if hasattr(self, 'gpt_key_input'):
                self.gpt_key_input.setText(config.get('gpt_key', ''))

            # Language config
            if hasattr(self, 'source_lang_combo'):
                src = config.get('source_lang', 'auto')
                idx = self.source_lang_combo.findData(src)
                if idx >= 0:
                    self.source_lang_combo.setCurrentIndex(idx)
            if hasattr(self, 'target_lang_combo'):
                tgt = config.get('target_lang', 'pt-BR')
                idx = self.target_lang_combo.findData(tgt)
                if idx >= 0:
                    self.target_lang_combo.setCurrentIndex(idx)
            if hasattr(self, 'gemini_key_input'):
                self.gemini_key_input.setText(config.get('gemini_key', ''))

            # Batch size: map from old config to new slider
            batch = config.get('ass_batch_size', config.get('srt_batch_size', 0))
            if batch in self._BATCH_MAP:
                self.batch_size_slider.setValue(self._BATCH_MAP.index(batch))
            else:
                self.batch_size_slider.setValue(0)
            self._on_batch_slider_changed(self.batch_size_slider.value())

            # TranslationProfile fields
            self.context_window_spin.setValue(int(config.get('context_window_size', 3)))
            self.enable_contextual_prompt_cb.setChecked(bool(config.get('enable_contextual_prompt', True)))
            self.enable_fewshot_cb.setChecked(bool(config.get('enable_fewshot', True)))
            self.enable_auto_glossary_cb.setChecked(bool(config.get('enable_auto_glossary', True)))
            if hasattr(self, 'glossary_model_combo'):
                gm = config.get('glossary_model', 'qwen2.5:7b-instruct')
                self._saved_glossary_model = gm
                self.glossary_model_combo.setCurrentText(gm)

            if hasattr(self, 'num_ctx_combo'):
                self.num_ctx_combo.setCurrentText(str(config.get('num_ctx', 2048)))
            if hasattr(self, 'num_thread_spin'):
                self.num_thread_spin.setValue(int(config.get('num_thread', 0)))

            if hasattr(self, 'model_info_label'):
                m = config.get('ollama_model', 'qwen2.5:32b-instruct-q4_K_M')
                self.model_info_label.setText(f'Model: {m}')

            if hasattr(self, 'cache_stats_label'):
                QTimer.singleShot(500, self.update_cache_stats)

            if config.get('sonarr_url') and config.get('sonarr_api_key'):
                QTimer.singleShot(1000, self.auto_connect_sonarr)
        except Exception:
            pass

    def save_config(self):
        batch_val = self._BATCH_MAP[self.batch_size_slider.value()]
        config = {
            'sonarr_url': self.sonarr_url.text(),
            'sonarr_api_key': self.sonarr_api_key.text(),
            'ollama_url': self.ollama_url.text(),
            'ollama_model': self._get_effective_ollama_model(),
            'parallelism': self.parallelism.value(),
            'skip_existing': self.skip_existing_cb.isChecked(),
            'use_micro_batch_2': batch_val >= 2,
            'use_batch_prompt': batch_val >= 4,
            'srt_batch_size': batch_val if batch_val >= 4 else 0,
            'ass_batch_size': batch_val if batch_val >= 2 else 1,
            'enable_webhook': self.enable_webhook_cb.isChecked(),
            'notify_done': self.notify_done_cb.isChecked() if hasattr(self, 'notify_done_cb') else True,
            'review_before_save': self.review_before_save_cb.isChecked() if hasattr(self, 'review_before_save_cb') else False,
            'api': self.api_selector.currentText() if hasattr(self, 'api_selector') else 'Ollama',
            # TranslationProfile fields
            'context_window_size': self.context_window_spin.value(),
            'enable_contextual_prompt': self.enable_contextual_prompt_cb.isChecked(),
            'enable_fewshot': self.enable_fewshot_cb.isChecked(),
            'enable_auto_glossary': self.enable_auto_glossary_cb.isChecked(),
            'num_ctx': int(self.num_ctx_combo.currentText()),
            'num_thread': self.num_thread_spin.value(),
            'glossary_model': self.glossary_model_combo.currentText().strip() if hasattr(self, 'glossary_model_combo') else 'qwen2.5:7b-instruct',
            # External API keys
            'deepl_key': self.deepl_key_input.text().strip() if hasattr(self, 'deepl_key_input') else '',
            'gpt_key': self.gpt_key_input.text().strip() if hasattr(self, 'gpt_key_input') else '',
            'gemini_key': self.gemini_key_input.text().strip() if hasattr(self, 'gemini_key_input') else '',
            # Language config
            'source_lang': self.source_lang_combo.currentData() if hasattr(self, 'source_lang_combo') else 'auto',
            'target_lang': self.target_lang_combo.currentData() if hasattr(self, 'target_lang_combo') else 'pt-BR',
        }
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}")

    # ── Auto-connect ──

    def auto_connect_sonarr(self):
        try:
            url = self.sonarr_url.text().strip()
            api_key = self.sonarr_api_key.text().strip()
            if url and api_key:
                self.status_bar.showMessage('Auto-connecting...')
                self.sonarr_client = SonarrClient(url, api_key)
                if self.sonarr_client.test_connection():
                    self.connection_status.setText('Connected')
                    self.connection_status.setStyleSheet(self.theme.get_status_badge_style('connected'))
                    self.sonarr_badge.setText('Connected')
                    self.sonarr_badge.setStyleSheet(self.theme.get_status_badge_style('connected'))
                    self.load_series_btn.setEnabled(True)
                    QTimer.singleShot(500, self.auto_load_series)
                else:
                    self.connection_status.setText('Failed')
                    self.connection_status.setStyleSheet(self.theme.get_status_badge_style('error'))
        except Exception as e:
            self.connection_status.setText('Error')
            self.connection_status.setStyleSheet(self.theme.get_status_badge_style('error'))
            self.connection_status.setToolTip(str(e))

    def auto_load_series(self):
        try:
            if self.sonarr_client:
                self.status_bar.showMessage('Loading series...')
                series_data = self.sonarr_client.get_series_with_files()
                for series in series_data:
                    episodes = self.sonarr_client.get_episodes(series['id'])
                    episode_files = self.sonarr_client.get_episode_files(series['id'])
                    file_lookup = {f['id']: f for f in episode_files}
                    eps_with_files = []
                    for ep in episodes:
                        if ep.get('hasFile') and ep.get('episodeFileId'):
                            fd = file_lookup.get(ep['episodeFileId'])
                            if fd:
                                eps_with_files.append({'id': ep['id'], 'video_path': fd.get('path')})
                    series['translation_stats'] = self.translation_tracker.get_series_stats(series['id'], eps_with_files)
                self.populate_series_grid(series_data)
                self.status_bar.showMessage(f'Loaded {len(series_data)} series')
                self.tab_widget.setCurrentIndex(0)
        except Exception as e:
            self.status_bar.showMessage(f'Auto-load failed: {str(e)}')

    # ── Model management ──

    def _get_effective_ollama_model(self):
        text = self.ollama_model_combo.currentText().strip()
        for i in range(self.ollama_model_combo.count()):
            if self.ollama_model_combo.itemData(i) == text:
                return text
            if self.ollama_model_combo.itemText(i) == text:
                return self.ollama_model_combo.itemData(i) or text
        return text or 'qwen2.5:32b-instruct-q4_K_M'

    def init_model_selector(self):
        self.refresh_ollama_models()

    def refresh_ollama_models(self):
        """Busca todos os modelos instalados no Ollama via /api/tags e popula o combo."""
        url = self.ollama_url.text().strip().rstrip('/') or 'http://localhost:11434'
        saved = self.ollama_model.text() if self.ollama_model.text() else 'qwen2.5:32b-instruct-q4_K_M'
        models_data = []
        self.ollama_model_combo.blockSignals(True)
        self.ollama_model_combo.clear()
        try:
            resp = requests.get(f"{url}/api/tags", timeout=5)
            if resp.status_code == 200:
                models_data = resp.json().get('models', [])
                for m in models_data:
                    name = m.get('name', '')
                    size_bytes = m.get('size', 0)
                    size_gb = size_bytes / (1024 ** 3)
                    display = f"{name}  ({size_gb:.1f} GB)" if size_gb >= 0.1 else name
                    self.ollama_model_combo.addItem(display, name)
        except Exception:
            pass

        if self.ollama_model_combo.count() == 0:
            self.ollama_model_combo.addItem(saved, saved)

        found = False
        for i in range(self.ollama_model_combo.count()):
            if self.ollama_model_combo.itemData(i) == saved:
                self.ollama_model_combo.setCurrentIndex(i)
                found = True
                break
        if not found:
            self.ollama_model_combo.setCurrentText(saved)
            self.ollama_model.setText(saved)
        self.ollama_model_combo.blockSignals(False)

        # Popular Glossary Model combo com os mesmos modelos
        if hasattr(self, 'glossary_model_combo'):
            saved_glossary = (self.glossary_model_combo.currentText().strip() or getattr(self, '_saved_glossary_model', None) or 'qwen2.5:7b-instruct')
            self.glossary_model_combo.blockSignals(True)
            self.glossary_model_combo.clear()
            for m in models_data:
                name = m.get('name', '')
                self.glossary_model_combo.addItem(name, name)
            if self.glossary_model_combo.count() == 0:
                self.glossary_model_combo.addItem(saved_glossary, saved_glossary)
            self.glossary_model_combo.setCurrentText(saved_glossary)
            self.glossary_model_combo.blockSignals(False)

    def display_hardware_info(self):
        if not self.hardware_detector:
            self.hardware_info_label.setText('Hardware detection unavailable')
            self.hardware_info_label.setStyleSheet(self.theme.get_status_badge_style('disconnected'))
            return
        info = self.hardware_detector.get_hardware_info()
        gpu = "GPU ✓" if info['has_gpu'] else "No GPU"
        vram = f" · VRAM: {info['vram_gb']}GB" if info['vram_gb'] > 0 else ""
        hw = f"{gpu}{vram} · RAM: {info['ram_gb']}GB · CPU: {info['cpu_cores']} cores"
        self.hardware_info_label.setText(hw)
        status = 'connected' if info['has_gpu'] else 'info'
        self.hardware_info_label.setStyleSheet(self.theme.get_status_badge_style(status))

    def on_model_selection_changed(self):
        effective = self._get_effective_ollama_model()
        self.ollama_model.setText(effective)
        self.save_config()
        self.auto_test_api()

    def on_model_text_changed(self):
        effective = self._get_effective_ollama_model()
        self.ollama_model.setText(effective)
        self.save_config()

    def download_selected_model(self):
        if not self.hardware_detector:
            QMessageBox.warning(self, "Error", "Hardware detector not available")
            return
        model_name = self._get_effective_ollama_model()
        ollama_url = self.ollama_url.text() or 'http://localhost:11434'
        if self.hardware_detector.is_model_available(ollama_url, model_name):
            QMessageBox.information(self, "Info", f"{model_name} is already available!")
            return
        reply = QMessageBox.question(self, "Download", f"Download {model_name}?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.download_model_btn.setEnabled(False)
            self.download_model_btn.setText("...")
            self.log_area.append(f"📥 Downloading {model_name}...")
            self._download_worker = ModelDownloadWorker(ollama_url, model_name, self.hardware_detector)
            self._download_worker.log_signal.connect(lambda msg: self.log_area.append(msg))
            self._download_worker.finished_signal.connect(self._on_model_download_finished)
            self._download_worker.start()

    def _on_model_download_finished(self, success):
        model_name = getattr(self._download_worker, 'model_name', '') if hasattr(self, '_download_worker') else ''
        self.download_model_btn.setEnabled(True)
        self.download_model_btn.setText("Download")
        if success:
            self.log_area.append(f"✅ {model_name} downloaded!")
            QMessageBox.information(self, "Success", f"{model_name} is ready!")
            self.auto_test_api()
            if hasattr(self, 'model_info_label'):
                self.model_info_label.setText(f'Model: {model_name} ✓')
                self.model_info_label.setStyleSheet(f"color: {self.theme.colors['success']}; font-size: 11px;")
        else:
            self.log_area.append(f"❌ Failed to download {model_name}")
            QMessageBox.critical(self, "Error", f"Failed to download {model_name}.\nCheck Ollama is running.")

    def import_local_gguf(self):
        default_dir = getattr(self, '_last_gguf_dir', None) or os.path.expanduser("~")
        if not os.path.isdir(default_dir):
            default_dir = os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, "Select GGUF file", default_dir, "GGUF (*.gguf);;All (*)")
        if not path or not path.strip():
            return
        self._last_gguf_dir = os.path.dirname(path)
        self.import_gguf_btn.setEnabled(False)
        self.import_gguf_btn.setText("Creating...")
        self.log_area.append(f"📂 Importing: {os.path.basename(path)}")
        self._import_gguf_worker = ImportGGUFWorker(path, model_name="qwen2.5:32b-instruct-q4_K_M")
        self._import_gguf_worker.log_signal.connect(lambda msg: self.log_area.append(msg))
        self._import_gguf_worker.finished_signal.connect(self._on_import_gguf_finished)
        self._import_gguf_worker.start()

    def _on_import_gguf_finished(self, success, message):
        self.import_gguf_btn.setEnabled(True)
        self.import_gguf_btn.setText("Import GGUF")
        if success:
            self.log_area.append(f"✅ {message}")
            QMessageBox.information(self, "Success", message)
            self.auto_test_api()
            if hasattr(self, 'init_model_selector'):
                self.init_model_selector()
            if hasattr(self, 'model_info_label'):
                self.model_info_label.setText("Model: qwen2.5:32b-instruct-q4_K_M ✓")
                self.model_info_label.setStyleSheet(f"color: {self.theme.colors['success']}; font-size: 11px;")
            self.ollama_model_combo.setCurrentText("qwen2.5:32b-instruct-q4_K_M")
            self.ollama_model.setText("qwen2.5:32b-instruct-q4_K_M")
        else:
            self.log_area.append(f"❌ GGUF import: {message}")
            QMessageBox.critical(self, "Error", f"Failed to create model.\n\n{message}")


def run_sonarr_gui():
    app = QApplication(sys.argv)
    theme = ModernTheme()
    theme.apply_theme(app)
    setup_fonts(app)
    icon_path = get_icon_path('icon')
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))
    window = SonarrSubtitleTranslator()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    current_dir = Path(__file__).parent
    if str(current_dir) not in sys.path:
        sys.path.insert(0, str(current_dir))
    run_sonarr_gui()
