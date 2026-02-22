from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QTreeWidget, QTreeWidgetItem, QDialogButtonBox, QMenu,
                              QMessageBox, QProgressBar, QComboBox)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction
from typing import Dict, List, Any, Optional
from .sonarr_client import SonarrClient
from .translation_tracker import TranslationTracker
from .modern_theme import ModernTheme, SPACING_SM, SPACING_MD


class _TrackLoaderWorker(QThread):
    """Loads subtitle tracks for a single video file in background."""
    finished = Signal(str, list)  # video_path, tracks

    def __init__(self, video_path: str):
        super().__init__()
        self._video_path = video_path

    def run(self):
        try:
            from pathlib import Path
            from .extractor import SubtitleExtractor

            class _NullLogger:
                def log(self, *a, **kw):
                    pass

            extractor = SubtitleExtractor(_NullLogger())
            result = extractor.parse_mkv_tracks_from_file(self._video_path)
            self.finished.emit(self._video_path, result)
        except Exception:
            self.finished.emit(self._video_path, [])


class EpisodeDetailsDialog(QDialog):
    """Professional episode management dialog with streamlined translation controls."""

    _theme = ModernTheme()

    def __init__(self, series_data: Dict[str, Any], sonarr_client: SonarrClient,
                 translation_tracker: TranslationTracker, parent: Optional[QDialog] = None):
        super().__init__(parent)
        self.series_data = series_data
        self.sonarr_client = sonarr_client
        self.translation_tracker = translation_tracker
        self.setWindowTitle(f"{series_data['title']} — Episodes")
        self.setMinimumSize(950, 600)

        # track_selections: episode_id -> track_id (None = Auto)
        self.track_selections: Dict[int, Optional[int]] = {}
        # Keep references to workers to prevent GC
        self._track_workers: List[_TrackLoaderWorker] = []

        # Size proportional to parent
        if parent:
            pw, ph = parent.width(), parent.height()
            self.resize(int(pw * 0.75), int(ph * 0.75))

        self._setup_ui()
        self._load_episodes()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        layout.setSpacing(SPACING_SM)

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {self._theme.colors['background']};
                color: {self._theme.colors['text']};
            }}
            {self._theme.get_scrollbar_style()}
        """)

        # ── Header ──
        header = QHBoxLayout()
        title_label = QLabel(self.series_data['title'])
        title_label.setStyleSheet(self._theme.get_label_style('title'))
        header.addWidget(title_label)

        ep_count = QLabel(f"{self.series_data.get('episodeCount', '?')} episodes")
        ep_count.setStyleSheet(self._theme.get_label_style('caption'))
        header.addWidget(ep_count)
        header.addStretch()
        layout.addLayout(header)

        # ── Series progress bar ──
        stats = self.series_data.get('translation_stats', {})
        pct = stats.get('percentage', 0)
        translated = stats.get('translated', 0)
        total = stats.get('total', 0)

        progress_row = QHBoxLayout()
        self.series_progress = QProgressBar()
        self.series_progress.setFixedHeight(18)
        self.series_progress.setValue(int(pct))
        self.series_progress.setFormat(f"{translated}/{total} translated ({pct}%)")
        self.series_progress.setStyleSheet(self._theme.get_progress_bar_style())
        progress_row.addWidget(self.series_progress)
        layout.addLayout(progress_row)

        # ── Episode tree ──
        self.episode_tree = QTreeWidget()
        self.episode_tree.setHeaderLabels(['Episode', 'Title', 'File', 'Translation', 'Subtitle Track'])
        self.episode_tree.setAlternatingRowColors(True)
        self.episode_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.episode_tree.customContextMenuRequested.connect(self._show_context_menu)
        self.episode_tree.setStyleSheet(f"""
            QTreeWidget {{
                background-color: {self._theme.colors['surface']};
                border: 1px solid {self._theme.colors['border']};
                border-radius: 6px;
                color: {self._theme.colors['text']};
                font-size: 13px;
            }}
            QTreeWidget::item {{
                padding: 4px 2px;
            }}
            QTreeWidget::item:selected {{
                background-color: {self._theme.colors['primary']};
                color: white;
            }}
            QHeaderView::section {{
                background-color: {self._theme.colors['surface_variant']};
                color: {self._theme.colors['text']};
                border: none;
                border-bottom: 1px solid {self._theme.colors['border']};
                padding: 8px;
                font-weight: 600;
                font-size: 12px;
            }}
        """)
        self.episode_tree.setColumnWidth(0, 80)
        self.episode_tree.setColumnWidth(1, 280)
        self.episode_tree.setColumnWidth(2, 110)
        self.episode_tree.setColumnWidth(3, 120)
        self.episode_tree.setColumnWidth(4, 200)
        layout.addWidget(self.episode_tree, 1)

        # ── Action buttons (2 only) ──
        btn_row = QHBoxLayout()

        self.translate_selected_btn = QPushButton('Translate Selected')
        self.translate_selected_btn.setFixedHeight(40)
        self.translate_selected_btn.setStyleSheet(self._theme.get_button_style('primary'))
        self.translate_selected_btn.clicked.connect(self._translate_selected)
        btn_row.addWidget(self.translate_selected_btn)

        self.translate_all_btn = QPushButton('Translate All')
        self.translate_all_btn.setFixedHeight(40)
        self.translate_all_btn.setStyleSheet(self._theme.get_button_style('success'))
        self.translate_all_btn.clicked.connect(self._translate_all)
        btn_row.addWidget(self.translate_all_btn)

        btn_row.addStretch()

        close_btn = QPushButton('Close')
        close_btn.setFixedHeight(40)
        close_btn.setStyleSheet(self._theme.get_button_style('secondary'))
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _load_episodes(self) -> None:
        try:
            episodes = self.sonarr_client.get_episodes(self.series_data['id'])
            episode_files = self.sonarr_client.get_episode_files(self.series_data['id'])

            seasons: Dict[int, List[Dict[str, Any]]] = {}
            for ep in episodes:
                sn = ep.get('seasonNumber', 0)
                seasons.setdefault(sn, []).append(ep)

            file_lookup = {f['id']: f for f in episode_files}

            for sn in sorted(seasons.keys()):
                if sn == 0:
                    continue

                season_item = QTreeWidgetItem([f"Season {sn}", "", "", ""])
                season_item.setData(0, Qt.UserRole, {'type': 'season', 'season': sn})
                self.episode_tree.addTopLevelItem(season_item)

                for ep in sorted(seasons[sn], key=lambda x: x.get('episodeNumber', 0)):
                    ep_num = ep.get('episodeNumber', 0)
                    title = ep.get('title', 'Unknown')
                    has_file = ep.get('hasFile', False)
                    file_status = "● Downloaded" if has_file else "○ Missing"

                    video_path = None
                    if has_file and ep.get('episodeFileId'):
                        fd = file_lookup.get(ep['episodeFileId'])
                        if fd and fd.get('path'):
                            video_path = fd['path']

                    trans = self.translation_tracker.get_episode_status(
                        self.series_data['id'], ep.get('id'), video_path)

                    trans_display = {
                        'translated': "● Translated",
                        'processing': "◉ Processing",
                        'not_translated': "○ Not Translated"
                    }.get(trans, "○ Not Translated")

                    item = QTreeWidgetItem([f"E{ep_num:02d}", title, file_status, trans_display, ""])
                    item.setData(0, Qt.UserRole, {
                        'type': 'episode',
                        'episode': ep,
                        'file_data': file_lookup.get(ep.get('episodeFileId')),
                        'translation_status': trans,
                        'video_path': video_path,
                    })

                    if has_file:
                        item.setCheckState(0, Qt.Unchecked)

                    season_item.addChild(item)

                    # Add track combo — start with "Loading..." if file exists
                    track_combo = QComboBox()
                    track_combo.addItem("Auto (programa escolhe)", None)
                    if video_path and video_path.lower().endswith('.mkv'):
                        track_combo.addItem("Carregando...", "loading")
                        track_combo.setEnabled(False)
                        ep_id = ep.get('id')
                        track_combo.setProperty('episode_id', ep_id)
                        self.episode_tree.setItemWidget(item, 4, track_combo)
                        # Lazy load tracks in background
                        self._load_tracks_for_item(item, video_path, track_combo, ep_id)
                    else:
                        track_combo.setEnabled(False)
                        self.episode_tree.setItemWidget(item, 4, track_combo)

                season_item.setExpanded(True)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load episodes: {str(e)}")

    def _load_tracks_for_item(self, item: QTreeWidgetItem, video_path: str,
                              combo: QComboBox, ep_id: int) -> None:
        worker = _TrackLoaderWorker(video_path)
        worker.finished.connect(lambda vp, tracks, i=item, c=combo, eid=ep_id:
                                self._on_tracks_loaded(i, c, eid, tracks))
        self._track_workers.append(worker)
        worker.start()

    def _on_tracks_loaded(self, item: QTreeWidgetItem, combo: QComboBox,
                          ep_id: int, tracks: list) -> None:
        combo.clear()
        combo.addItem("Auto (programa escolhe)", None)
        for t in tracks:
            lang = t.get('language', 'und').upper()
            codec = t.get('codec_id', '').replace('S_TEXT/', '')
            name = t.get('name', '')
            label = f"Track {t['id']} — {lang} {codec}"
            if name:
                label += f" ({name})"
            combo.addItem(label, t['id'])
        combo.setEnabled(True)

        def _on_combo_changed(idx, c=combo, eid=ep_id):
            self.track_selections[eid] = c.currentData()

        combo.currentIndexChanged.connect(_on_combo_changed)

    def _show_context_menu(self, position) -> None:
        item = self.episode_tree.itemAt(position)
        if not item:
            return
        data = item.data(0, Qt.UserRole)
        if not data or data.get('type') != 'episode':
            return

        menu = QMenu(self)
        current = data.get('translation_status', 'not_translated')

        actions = [
            ('translated', '● Mark as Translated'),
            ('processing', '◉ Mark as Processing'),
            ('not_translated', '○ Mark as Not Translated'),
        ]
        for status_val, label in actions:
            if current != status_val:
                act = QAction(label, self)
                act.triggered.connect(lambda checked, i=item, s=status_val: self._set_status(i, s))
                menu.addAction(act)

        menu.exec(self.episode_tree.mapToGlobal(position))

    def _set_status(self, item: QTreeWidgetItem, status: str) -> None:
        data = item.data(0, Qt.UserRole)
        episode = data.get('episode')
        file_data = data.get('file_data')
        video_path = file_data.get('path') if file_data else None

        self.translation_tracker.mark_episode_status(
            self.series_data['id'], episode.get('id'), status, video_path)

        display = {
            'translated': "● Translated",
            'processing': "◉ Processing",
            'not_translated': "○ Not Translated"
        }.get(status, "○ Not Translated")
        item.setText(3, display)
        data['translation_status'] = status
        item.setData(0, Qt.UserRole, data)

    # ── Episode collection helpers ──

    def _get_selected_episodes(self) -> List[Dict[str, Any]]:
        selected = []
        for i in range(self.episode_tree.topLevelItemCount()):
            season = self.episode_tree.topLevelItem(i)
            for j in range(season.childCount()):
                child = season.child(j)
                if child.checkState(0) == Qt.Checked:
                    data = child.data(0, Qt.UserRole)
                    if data and data.get('type') == 'episode':
                        selected.append(data)
        return selected

    def _get_all_episodes(self) -> List[Dict[str, Any]]:
        episodes = []
        for i in range(self.episode_tree.topLevelItemCount()):
            season = self.episode_tree.topLevelItem(i)
            for j in range(season.childCount()):
                child = season.child(j)
                data = child.data(0, Qt.UserRole)
                if data and data.get('type') == 'episode' and data.get('file_data'):
                    episodes.append(data)
        return episodes

    # ── Translation actions (unified confirmation) ──

    def _confirm_and_start(self, episodes: List[Dict[str, Any]], scope: str) -> None:
        already = []
        for ep_data in episodes:
            ep = ep_data.get('episode', {})
            eid = ep.get('id')
            if eid:
                st = self.translation_tracker.get_episode_status(self.series_data['id'], eid)
                if st == 'translated':
                    already.append(ep.get('title', f"E{ep.get('episodeNumber', '?')}"))

        if already:
            preview = "\n".join([f"  • {t}" for t in already[:5]])
            if len(already) > 5:
                preview += f"\n  • ... +{len(already) - 5} more"
            reply = QMessageBox.question(
                self, "Overwrite?",
                f"Translate {scope} ({len(episodes)} episodes)?\n\n"
                f"{len(already)} already translated:\n{preview}\n\n"
                "Existing translations will be overwritten.",
                QMessageBox.Yes | QMessageBox.No)
        else:
            reply = QMessageBox.question(
                self, "Confirm",
                f"Translate {scope}?\n\n{len(episodes)} episodes will be processed.",
                QMessageBox.Yes | QMessageBox.No)

        if reply == QMessageBox.Yes:
            self.accept()
            if hasattr(self.parent(), 'start_episode_processing'):
                self.parent().start_episode_processing(
                    episodes, self.series_data,
                    track_selections=self.track_selections,
                )

    def _translate_selected(self) -> None:
        episodes = self._get_selected_episodes()
        if not episodes:
            QMessageBox.warning(self, "Warning", "Select episodes to translate (use checkboxes)")
            return
        self._confirm_and_start(episodes, f"Selected ({len(episodes)})")

    def _translate_all(self) -> None:
        episodes = self._get_all_episodes()
        if not episodes:
            QMessageBox.warning(self, "Warning", "No episodes available for translation")
            return
        self._confirm_and_start(episodes, "All Episodes")
