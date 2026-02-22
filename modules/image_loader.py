import requests
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QPixmap


class ImageLoader(QThread):
    """Asynchronous image loader for series posters with configurable size."""

    image_loaded = Signal(int, QPixmap)

    def __init__(self, series_id: int, url: str, width: int = 120, height: int = 180):
        super().__init__()
        self.series_id = series_id
        self.url = url
        self.width = width
        self.height = height

    def run(self) -> None:
        try:
            response = requests.get(self.url, timeout=10)
            response.raise_for_status()
            pixmap = QPixmap()
            if pixmap.loadFromData(response.content) and not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.width, self.height,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                self.image_loaded.emit(self.series_id, scaled)
        except requests.RequestException:
            pass
        except Exception:
            pass
