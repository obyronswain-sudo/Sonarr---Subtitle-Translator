"""
Translation cache system using SQLite for performance.
v2: hash contextual (texto + linhas adjacentes) com migração suave de v1.
"""
import re
import sqlite3
import hashlib
import json
from pathlib import Path
import threading
from contextlib import contextmanager

class TranslationCache:
    def __init__(self, cache_file='translation_cache.db'):
        self.cache_file = Path(cache_file)
        self.lock = threading.Lock()
        self._init_db()
    
    def _init_db(self):
        """Initialize the cache database"""
        with self.lock:
            with sqlite3.connect(self.cache_file) as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS translations (
                        text_hash TEXT PRIMARY KEY,
                        original_text TEXT NOT NULL,
                        translated_text TEXT NOT NULL,
                        source_lang TEXT NOT NULL,
                        target_lang TEXT NOT NULL,
                        api_used TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        hit_count INTEGER DEFAULT 1
                    )
                ''')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_hash ON translations(text_hash)')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_langs ON translations(source_lang, target_lang)')
    
    def _get_text_hash(self, text, source_lang, target_lang):
        """Generate v1 hash for text + language pair (backward compat)."""
        normalized = re.sub(r'\s+', ' ', text.strip()).lower()
        content = f"{normalized}|{source_lang}|{target_lang}"
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    def _get_text_hash_v2(self, text, source_lang, target_lang,
                          prev_line="", next_line=""):
        """
        Generate v2 contextual hash: texto + linhas adjacentes.
        Melhora a qualidade do cache capturando contexto local.
        """
        normalized = re.sub(r'\s+', ' ', text.strip()).lower()
        prev_norm = re.sub(r'\s+', ' ', prev_line.strip()).lower() if prev_line else ""
        next_norm = re.sub(r'\s+', ' ', next_line.strip()).lower() if next_line else ""
        content = f"{normalized}|{prev_norm}|{next_norm}|{source_lang}|{target_lang}|v2"
        return hashlib.md5(content.encode('utf-8')).hexdigest()
    
    @contextmanager
    def _get_connection(self):
        """Thread-safe database connection"""
        with self.lock:
            conn = sqlite3.connect(self.cache_file)
            try:
                yield conn
            finally:
                conn.close()
    
    def get(self, text, source_lang='en', target_lang='pt-BR',
            prev_line="", next_line=""):
        """
        Get cached translation with dual-read: v2 first, then v1 fallback.
        Se encontrar em v1 e não em v2, promove para v2 em background.
        """
        if not text or len(text.strip()) < 3:
            return None

        # Tentar v2 primeiro (contextual)
        hash_v2 = self._get_text_hash_v2(text, source_lang, target_lang, prev_line, next_line)
        result = self._get_by_hash(hash_v2)
        if result:
            return result

        # Fallback para v1 (sem contexto)
        hash_v1 = self._get_text_hash(text, source_lang, target_lang)
        result = self._get_by_hash(hash_v1)
        if result:
            # Promover para v2: salvar com hash contextual
            self._save_by_hash(hash_v2, text, result, source_lang, target_lang, 'cache_v1_promote')
            return result

        return None

    def _get_by_hash(self, text_hash):
        """Get translation by hash key."""
        with self._get_connection() as conn:
            try:
                cursor = conn.execute(
                    'UPDATE translations SET hit_count = hit_count + 1 WHERE text_hash = ? RETURNING translated_text',
                    (text_hash,)
                )
                row = cursor.fetchone()
                if row:
                    conn.commit()
                    return row[0]
            except sqlite3.OperationalError:
                pass

            cursor = conn.execute(
                'SELECT translated_text FROM translations WHERE text_hash = ?',
                (text_hash,)
            )
            result = cursor.fetchone()
            if result:
                conn.execute(
                    'UPDATE translations SET hit_count = hit_count + 1 WHERE text_hash = ?',
                    (text_hash,)
                )
                conn.commit()
                return result[0]
        return None

    def _save_by_hash(self, text_hash, original_text, translated_text,
                      source_lang, target_lang, api_used):
        """Save translation by hash key."""
        with self._get_connection() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO translations 
                (text_hash, original_text, translated_text, source_lang, target_lang, api_used)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (text_hash, original_text.strip(), translated_text.strip(),
                  source_lang, target_lang, api_used))
            conn.commit()

    def set(self, original_text, translated_text, source_lang='en', target_lang='pt-BR',
            api_used='unknown', prev_line="", next_line=""):
        """Cache a translation with both v1 and v2 hashes."""
        if not original_text or not translated_text or len(original_text.strip()) < 3:
            return
        
        # Don't cache if translation didn't change
        if original_text.strip() == translated_text.strip():
            return
        
        # Save v1 (para compatibilidade)
        hash_v1 = self._get_text_hash(original_text, source_lang, target_lang)
        self._save_by_hash(hash_v1, original_text, translated_text,
                           source_lang, target_lang, api_used)

        # Save v2 (contextual)
        hash_v2 = self._get_text_hash_v2(original_text, source_lang, target_lang,
                                          prev_line, next_line)
        if hash_v2 != hash_v1:
            self._save_by_hash(hash_v2, original_text, translated_text,
                               source_lang, target_lang, api_used)
    
    def get_stats(self):
        """Get cache statistics"""
        with self._get_connection() as conn:
            cursor = conn.execute('SELECT COUNT(*), SUM(hit_count) FROM translations')
            total_entries, total_hits = cursor.fetchone()
            
            cursor = conn.execute('SELECT api_used, COUNT(*) FROM translations GROUP BY api_used')
            api_stats = dict(cursor.fetchall())
            
            return {
                'total_entries': total_entries or 0,
                'total_hits': total_hits or 0,
                'api_breakdown': api_stats
            }
    
    def cleanup_old_entries(self, days=30):
        """Remove old cache entries"""
        with self._get_connection() as conn:
            conn.execute(
                'DELETE FROM translations WHERE created_at < datetime("now", "-" || ? || " days")',
                (int(days),)
            )
            conn.commit()
    
    def cleanup_bad_translations(self):
        """Remove cached translations that are identical to original (non-translations)"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                '''SELECT text_hash, original_text, translated_text 
                   FROM translations 
                   WHERE LOWER(TRIM(original_text)) = LOWER(TRIM(translated_text))'''
            )
            bad_entries = cursor.fetchall()
            
            if bad_entries:
                conn.execute(
                    '''DELETE FROM translations 
                       WHERE LOWER(TRIM(original_text)) = LOWER(TRIM(translated_text))'''
                )
                conn.commit()
                return len(bad_entries)
            return 0
    
    def clear_cache(self):
        """Clear all cache entries"""
        with self._get_connection() as conn:
            conn.execute('DELETE FROM translations')
            conn.commit()