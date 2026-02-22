from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QProgressBar
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QPixmap, QPainter, QBrush, QPen
from typing import Dict, Any, Optional
from .image_loader import ImageLoader


class ModernAnimeCard(QFrame):
    """Modern anime card widget with poster, info, and progress display"""

    clicked = Signal(dict)
    quick_translate = Signal(dict)
    show_episodes = Signal(dict)

    def __init__(self, series_data: Dict[str, Any], parent: Optional[QFrame] = None):
        super().__init__(parent)
        self.series_data: Dict[str, Any] = series_data
        self.parent_window: Optional[QFrame] = parent
        self.is_hovered: bool = False
        self.is_processing: bool = False
        self.progress_value: float = 0
        self.setup_ui()
        self.setup_animations()

    def setup_ui(self) -> None:
        """Setup the card UI components"""
        # Calculate responsive size based on parent window
        parent_width = self.parent_window.width() if self.parent_window else 1200
        card_width = max(140, min(180, parent_width // 8))  # Reduced from //6 to //8
        card_height = int(card_width * 1.4)  # Reduced aspect ratio

        self.setFixedSize(card_width, card_height)

        # Add tooltip with series info including subtitle stats
        tooltip_text = f"{self.series_data['title']}\n{self.series_data['episodeCount']} episodes"
        if self.series_data.get('year'):
            tooltip_text += f" ({self.series_data['year']})"
        if self.series_data.get('network'):
            tooltip_text += f"\nNetwork: {self.series_data['network']}"

        # Add translation statistics to tooltip
        if self.series_data.get('translation_stats'):
            stats = self.series_data['translation_stats']
            percentage = stats.get('percentage', 0)
            translated = stats.get('translated', 0)
            total = stats.get('total', 0)
            if total > 0:
                tooltip_text += f"\nTranslated: {translated}/{total} episodes ({percentage}%)"

        self.setToolTip(tooltip_text)

        self.setStyleSheet("""
            ModernAnimeCard {
                border: none;
                border-radius: 12px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(60, 60, 60, 180), stop:1 rgba(40, 40, 40, 200));
            }
            ModernAnimeCard:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(0, 120, 212, 200), stop:1 rgba(0, 100, 180, 220));
            }
        """)

        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Poster container
        self.poster_container = QFrame()
        self.poster_container.setStyleSheet("""
            QFrame {
                border-radius: 12px;
                background-color: rgba(45, 45, 45, 150);
            }
        """)
        poster_layout = QVBoxLayout(self.poster_container)
        poster_layout.setContentsMargins(0, 0, 0, 0)

        # Poster image - responsive size (smaller)
        poster_height = card_height - 15
        self.poster_label = QLabel()
        self.poster_label.setFixedSize(card_width, poster_height)
        self.poster_label.setStyleSheet("border-radius: 8px; background-color: rgba(45, 45, 45, 100);")
        self.poster_label.setAlignment(Qt.AlignCenter)
        self.poster_label.setText("Loading...")
        self.poster_label.setScaledContents(True)
        poster_layout.addWidget(self.poster_label)

        # Overlay container (hidden by default)
        self.overlay = QFrame(self.poster_container)
        self.overlay.setGeometry(0, 0, card_width, poster_height)
        self.overlay.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(0, 0, 0, 0), stop:0.7 rgba(0, 0, 0, 100), stop:1 rgba(0, 0, 0, 180));
                border-radius: 12px;
            }
        """)
        self.overlay.hide()

        # Overlay content
        overlay_layout = QVBoxLayout(self.overlay)
        overlay_layout.setContentsMargins(10, 10, 10, 10)
        overlay_layout.addStretch()

        # Title (smaller font)
        self.title_label = QLabel(self.series_data['title'])
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("""
            color: white;
            font-weight: bold;
            font-size: 12px;
            background: transparent;
        """)
        overlay_layout.addWidget(self.title_label)

        # Info with translation percentage (smaller font)
        info_text = f"{self.series_data['episodeCount']} episodes"
        if self.series_data.get('year'):
            info_text += f" • {self.series_data['year']}"

        # Add translation percentage if available
        if self.series_data.get('translation_stats'):
            stats = self.series_data['translation_stats']
            percentage = stats.get('percentage', 0)
            processing = stats.get('processing', 0)
            if percentage > 0:
                info_text += f" • {percentage}% Done"
            if processing > 0:
                info_text += f" • {processing} Processing"

        self.info_label = QLabel(info_text)
        self.info_label.setStyleSheet("""
            color: rgba(255, 255, 255, 180);
            font-size: 9px;
            background: transparent;
        """)
        overlay_layout.addWidget(self.info_label)

        # Progress bar (hidden by default)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                background-color: rgba(255, 255, 255, 50);
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4CAF50, stop:1 #8BC34A);
                border-radius: 2px;
            }
        """)
        self.progress_bar.hide()
        overlay_layout.addWidget(self.progress_bar)

        # Translation progress indicator (always visible if data available)
        if self.series_data.get('translation_stats'):
            stats = self.series_data['translation_stats']
            percentage = stats.get('percentage', 0)
            if stats.get('total', 0) > 0:
                self.translation_progress = QProgressBar()
                self.translation_progress.setFixedHeight(3)
                self.translation_progress.setValue(int(percentage))
                self.translation_progress.setStyleSheet("""
                    QProgressBar {
                        border: none;
                        background-color: rgba(255, 255, 255, 30);
                        border-radius: 1px;
                    }
                    QProgressBar::chunk {
                        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                            stop:0 #2196F3, stop:1 #03DAC6);
                        border-radius: 1px;
                    }
                """)
                overlay_layout.addWidget(self.translation_progress)

        layout.addWidget(self.poster_container)

        # Load poster image
        if self.series_data.get('poster'):
            self.image_loader = ImageLoader(self.series_data['id'], self.series_data['poster'])
            self.image_loader.image_loaded.connect(self.set_poster)
            self.image_loader.start()

    def setup_animations(self) -> None:
        """Setup hover animations (simplified)"""
        pass  # Remove problematic scale animation

    def enterEvent(self, event) -> None:
        """Handle mouse enter event"""
        self.is_hovered = True
        self.overlay.show()

        # Simple hover effect without position changes
        self.setStyleSheet("""
            ModernAnimeCard {
                border: 2px solid rgba(0, 120, 212, 150);
                border-radius: 12px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(0, 120, 212, 200), stop:1 rgba(0, 100, 180, 220));
            }
        """)

        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """Handle mouse leave event"""
        self.is_hovered = False
        if not self.is_processing:
            self.overlay.hide()

        # Reset to normal style
        self.setStyleSheet("""
            ModernAnimeCard {
                border: none;
                border-radius: 12px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(60, 60, 60, 180), stop:1 rgba(40, 40, 40, 200));
            }
        """)

        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        """Handle mouse press event"""
        if event.button() == Qt.LeftButton:
            self.show_episodes.emit(self.series_data)
        super().mousePressEvent(event)

    def set_poster(self, series_id: int, pixmap: QPixmap) -> None:
        """Set the poster image when loaded"""
        if series_id == self.series_data['id']:
            # Create rounded pixmap (smaller)
            rounded_pixmap = self.create_rounded_pixmap(pixmap, 8)
            self.poster_label.setPixmap(rounded_pixmap)
            self.poster_label.setText("")

    def create_rounded_pixmap(self, pixmap: QPixmap, radius: int) -> QPixmap:
        """Create a pixmap with rounded corners"""
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

    def start_processing(self) -> None:
        """Start processing animation"""
        self.is_processing = True
        self.progress_bar.show()
        self.overlay.show()
        self.progress_bar.setValue(0)

    def update_progress(self, value: float) -> None:
        """Update progress value"""
        self.progress_value = value
        self.progress_bar.setValue(int(value))

    def finish_processing(self) -> None:
        """Finish processing animation"""
        self.is_processing = False
        self.progress_bar.hide()
        if not self.is_hovered:
            self.overlay.hide()
