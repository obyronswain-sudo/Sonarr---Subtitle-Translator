import requests
import json
from pathlib import Path
from typing import List, Dict, Optional

class SonarrClient:
    def __init__(self, url: str, api_key: str, logger=None):
        self.url = url.rstrip('/')
        self.api_key = api_key
        self.logger = logger
        self.headers = {'X-Api-Key': api_key}
        
    def test_connection(self) -> bool:
        """Test connection to Sonarr"""
        try:
            response = requests.get(f"{self.url}/api/v3/system/status", headers=self.headers, timeout=10)
            return response.status_code == 200
        except Exception as e:
            if self.logger:
                self.logger.log('error', f'Erro conectando ao Sonarr: {e}')
            return False
    
    def get_series(self) -> List[Dict]:
        """Get all series from Sonarr"""
        try:
            response = requests.get(f"{self.url}/api/v3/series", headers=self.headers, timeout=30)
            if response.status_code == 200:
                return response.json()
            return []
        except Exception as e:
            if self.logger:
                self.logger.log('error', f'Erro obtendo séries: {e}')
            return []
    
    def get_series_episodes(self, series_id: int) -> List[Dict]:
        """Get episodes for a specific series"""
        try:
            response = requests.get(f"{self.url}/api/v3/episode?seriesId={series_id}", headers=self.headers, timeout=30)
            if response.status_code == 200:
                return response.json()
            return []
        except Exception as e:
            if self.logger:
                self.logger.log('error', f'Erro obtendo episódios: {e}')
            return []
    
    def get_episode_files(self, series_id: int) -> List[Dict]:
        """Get episode files for a series"""
        try:
            response = requests.get(f"{self.url}/api/v3/episodefile?seriesId={series_id}", headers=self.headers, timeout=30)
            if response.status_code == 200:
                return response.json()
            return []
        except Exception as e:
            if self.logger:
                self.logger.log('error', f'Erro obtendo arquivos: {e}')
            return []
    
    def get_series_with_files(self) -> List[Dict]:
        """Get series with their episode files"""
        series_list = self.get_series()
        result = []
        
        for series in series_list:
            if series.get('statistics', {}).get('episodeFileCount', 0) > 0:
                # Get poster image
                poster_url = None
                for image in series.get('images', []):
                    if image.get('coverType') == 'poster':
                        poster_url = f"{self.url}{image.get('url')}"
                        break
                
                series_data = {
                    'id': series['id'],
                    'title': series['title'],
                    'year': series.get('year'),
                    'path': series['path'],
                    'poster': poster_url,
                    'episodeCount': series.get('statistics', {}).get('episodeFileCount', 0),
                    'status': series.get('status'),
                    'network': series.get('network'),
                    'genres': series.get('genres', [])
                }
                result.append(series_data)
        
        return sorted(result, key=lambda x: x['title'])
    
    def get_episodes(self, series_id: int) -> List[Dict]:
        """Get episodes for a specific series (alias for get_series_episodes)"""
        return self.get_series_episodes(series_id)
    
    def get_series_files_paths(self, series_id: int) -> List[str]:
        """Get file paths for a specific series"""
        episode_files = self.get_episode_files(series_id)
        paths = []
        
        for file_info in episode_files:
            if 'path' in file_info:
                paths.append(file_info['path'])
        
        return paths