"""
Configuration loader for environment variables and .env files
"""
import os
from pathlib import Path
from typing import Dict, Any, Optional

class ConfigLoader:
    def __init__(self, env_file='.env'):
        self.env_file = Path(env_file)
        self.config = {}
        self._load_config()
    
    def _load_config(self):
        """Load configuration from .env file and environment variables"""
        # Load from .env file if it exists
        if self.env_file.exists():
            self._load_env_file()
        
        # Override with actual environment variables
        self._load_env_vars()
    
    def _load_env_file(self):
        """Load variables from .env file"""
        try:
            with open(self.env_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # Remove quotes if present
                        if value.startswith('"') and value.endswith('"'):
                            value = value[1:-1]
                        elif value.startswith("'") and value.endswith("'"):
                            value = value[1:-1]
                        
                        self.config[key] = value
        except Exception:
            pass  # Ignore errors loading .env file
    
    def _load_env_vars(self):
        """Load from actual environment variables"""
        env_vars = [
            'MKVEXTRACT_PATH', 'FFMPEG_PATH', 'TESSERACT_PATH',
            'DEEPL_API_KEY', 'OPENAI_API_KEY', 'GEMINI_API_KEY',
            'OLLAMA_URL', 'OLLAMA_MODEL', 'DEFAULT_TARGET_LANG',
            'CACHE_ENABLED', 'CACHE_MAX_ENTRIES', 'MIN_TRANSLATION_RATIO',
            'MIN_USEFUL_CHARS', 'ENABLE_LANGUAGE_DETECTION',
            'MAX_TRANSLATION_WORKERS', 'CHUNK_SIZE', 'REQUEST_TIMEOUT',
            'RETRY_ATTEMPTS', 'LOG_LEVEL', 'LOG_MAX_SIZE', 'LOG_BACKUP_COUNT'
        ]
        
        for var in env_vars:
            value = os.environ.get(var)
            if value is not None:
                self.config[var] = value
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value with type conversion"""
        value = self.config.get(key, default)
        
        if value is None:
            return default
        
        # Convert string values to appropriate types
        if isinstance(value, str):
            lower = value.strip().lower()

            # Boolean conversion
            if lower in ('true', 'yes', '1', 'on'):
                return True
            if lower in ('false', 'no', '0', 'off'):
                return False

            type_map = {
                # Integers
                'CACHE_MAX_ENTRIES': int,
                'MIN_USEFUL_CHARS': int,
                'MAX_TRANSLATION_WORKERS': int,
                'CHUNK_SIZE': int,
                'REQUEST_TIMEOUT': int,
                'RETRY_ATTEMPTS': int,
                'LOG_MAX_SIZE': int,
                'LOG_BACKUP_COUNT': int,
                # Floats
                'MIN_TRANSLATION_RATIO': float,
            }

            converter = type_map.get(key)
            if converter:
                try:
                    return converter(value)
                except ValueError:
                    return default
        
        return value
    
    def get_tool_paths(self) -> Dict[str, Optional[str]]:
        """Get tool paths configuration"""
        return {
            'mkvextract': self.get('MKVEXTRACT_PATH'),
            'ffmpeg': self.get('FFMPEG_PATH'),
            'tesseract': self.get('TESSERACT_PATH')
        }
    
    def get_api_keys(self) -> Dict[str, str]:
        """Get API keys configuration"""
        return {
            'deepl': self.get('DEEPL_API_KEY', ''),
            'gpt': self.get('OPENAI_API_KEY', ''),
            'gemini': self.get('GEMINI_API_KEY', ''),
            'ollama_url': self.get('OLLAMA_URL', 'http://localhost:11434'),
            'ollama_model': self.get('OLLAMA_MODEL', 'subtitle-translator')
        }
    
    def get_translation_settings(self) -> Dict[str, Any]:
        """Get translation settings"""
        return {
            'target_lang': self.get('DEFAULT_TARGET_LANG', 'pt-BR'),
            'cache_enabled': self.get('CACHE_ENABLED', True),
            'cache_max_entries': self.get('CACHE_MAX_ENTRIES', 10000),
            'min_translation_ratio': self.get('MIN_TRANSLATION_RATIO', 0.3),
            'min_useful_chars': self.get('MIN_USEFUL_CHARS', 100),
            'enable_language_detection': self.get('ENABLE_LANGUAGE_DETECTION', True)
        }
    
    def get_performance_settings(self) -> Dict[str, Any]:
        """Get performance settings"""
        return {
            'max_workers': self.get('MAX_TRANSLATION_WORKERS', 4),
            'chunk_size': self.get('CHUNK_SIZE', 3),
            'request_timeout': self.get('REQUEST_TIMEOUT', 60),
            'retry_attempts': self.get('RETRY_ATTEMPTS', 3)
        }
    
    def get_logging_settings(self) -> Dict[str, Any]:
        """Get logging settings"""
        return {
            'level': self.get('LOG_LEVEL', 'INFO'),
            'max_size': self.get('LOG_MAX_SIZE', 10485760),
            'backup_count': self.get('LOG_BACKUP_COUNT', 5)
        }