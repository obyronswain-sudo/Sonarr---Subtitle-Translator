import os
from pathlib import Path

class FileScanner:
    def __init__(self, base_dir, logger):
        self.base_dir = Path(base_dir)
        self.logger = logger
        self.video_extensions = ['.mkv', '.mp4', '.avi', '.mov', '.wmv']

    def scan_files(self):
        video_files = []
        for root, dirs, files in os.walk(self.base_dir):
            for file in files:
                if any(file.lower().endswith(ext) for ext in self.video_extensions):
                    path = Path(root) / file
                    # Evita caminhos "fantasmas" (arquivo removido durante o scan)
                    try:
                        if path.exists():
                            video_files.append(path)
                    except OSError:
                        continue
        self.logger.log('info', f'Encontrados {len(video_files)} arquivos de vídeo.')
        return video_files