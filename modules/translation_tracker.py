import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Any


class TranslationTracker:
    """Tracks translation status of episodes and series using dict-of-dicts format."""

    def __init__(self, cache_file: str = 'translation_index.json'):
        self.cache_file: str = cache_file
        self.translated_episodes: Dict[str, Dict[str, Dict[str, Any]]] = self.load_index()

    def load_index(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                if isinstance(data, dict):
                    migrated: Dict[str, Dict[str, Dict[str, Any]]] = {}
                    for series_key, value in data.items():
                        if isinstance(value, dict):
                            # Check if it's already dict-of-dicts or old list format stored as dict
                            first_val = next(iter(value.values()), None) if value else None
                            if isinstance(first_val, dict):
                                migrated[series_key] = value
                            else:
                                migrated[series_key] = {}
                            continue
                        if isinstance(value, list):
                            series_map: Dict[str, Dict[str, Any]] = {}
                            for ep in value:
                                if isinstance(ep, dict) and ep.get('episode_id') is not None:
                                    series_map[str(ep['episode_id'])] = ep
                            migrated[series_key] = series_map
                        else:
                            migrated[series_key] = {}
                    return migrated
            except Exception:
                return {}
        return {}

    def save_index(self) -> None:
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.translated_episodes, f, indent=2)
        except Exception as e:
            print(f"Error saving translation index: {e}")

    def detect_translated_file(self, video_path: str) -> str:
        video_path_obj = Path(video_path)
        if not video_path_obj.exists():
            return 'not_translated'

        base_name = video_path_obj.stem
        parent_dir = video_path_obj.parent

        pt_patterns = ['pt-BR', 'pt-br', 'portuguese', 'brasil', 'brazil']
        sub_extensions = ['.srt', '.ass', '.vtt']

        for pattern in pt_patterns:
            for ext in sub_extensions:
                if (parent_dir / f"{base_name}.{pattern}{ext}").exists():
                    return 'translated'

        return 'not_translated'

    def get_episode_status(self, series_id: int, episode_id: int, video_path: Optional[str] = None) -> str:
        series_key = str(series_id)
        episode_key = str(episode_id)

        series_map = self.translated_episodes.get(series_key)
        if isinstance(series_map, dict):
            episode_data = series_map.get(episode_key)
            if isinstance(episode_data, dict):
                status = episode_data.get('status')
                if status == 'processing':
                    return 'processing'
                if status == 'translated':
                    return 'translated'

        if video_path:
            detected = self.detect_translated_file(video_path)
            if detected == 'translated':
                self.mark_episode_status(series_id, episode_id, 'translated', video_path)
                return 'translated'

        return 'not_translated'

    def mark_episode_status(self, series_id: int, episode_id: int, status: str,
                            video_path: Optional[str] = None) -> None:
        series_key = str(series_id)
        episode_key = str(episode_id)

        if series_key not in self.translated_episodes or not isinstance(
                self.translated_episodes.get(series_key), dict):
            self.translated_episodes[series_key] = {}

        self.translated_episodes[series_key][episode_key] = {
            'episode_id': episode_key,
            'status': status,
            'timestamp': time.time(),
            'video_path': str(video_path) if video_path else None,
        }
        self.save_index()

    def get_series_stats(self, series_id: int, episodes_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not episodes_data:
            return {'percentage': 0, 'translated': 0, 'total': 0, 'processing': 0}

        total = len(episodes_data)
        translated = 0
        processing = 0

        for episode in episodes_data:
            video_path = episode.get('video_path')
            status = self.get_episode_status(series_id, episode.get('id'), video_path)
            if status == 'translated':
                translated += 1
            elif status == 'processing':
                processing += 1

        percentage = (translated / total * 100) if total > 0 else 0
        return {
            'percentage': round(percentage, 1),
            'translated': translated,
            'total': total,
            'processing': processing,
        }
