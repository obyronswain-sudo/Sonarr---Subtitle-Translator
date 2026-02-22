"""
Hybrid Cache System for Translation
Combines fast in-memory cache with persistent disk cache
"""
import sqlite3
import hashlib
import json
import threading
import time
import psutil
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
import logging

@dataclass
class CacheEntry:
    """Represents a cache entry with metadata"""
    text_hash: str
    original_text: str
    translated_text: str
    source_lang: str
    target_lang: str
    api_used: str
    created_at: float
    hit_count: int
    last_accessed: float

class HybridCache:
    """
    High-performance hybrid cache system with:
    - LRU in-memory cache for frequently accessed items
    - Persistent SQLite cache for long-term storage
    - Automatic cleanup and optimization
    """
    
    def __init__(self, cache_file='translation_cache.db', logger=None):
        self.cache_file = Path(cache_file)
        self.logger = logger
        
        # In-memory cache configuration
        self.memory_cache_size = self._calculate_memory_cache_size()
        self.memory_cache: Dict[str, CacheEntry] = {}
        self.access_order: List[str] = []  # For LRU tracking
        self.memory_hits = 0
        self.memory_misses = 0
        
        # Disk cache configuration
        self.disk_hits = 0
        self.disk_misses = 0
        
        # Threading
        self.lock = threading.RLock()
        self._init_disk_cache()
        
        if self.logger:
            self.logger.log('info', f'✅ Cache híbrido iniciado (Memória: {self.memory_cache_size} entradas, Disco: {cache_file})')
    
    def _calculate_memory_cache_size(self) -> int:
        """Calculate optimal in-memory cache size based on available RAM"""
        memory_gb = psutil.virtual_memory().total / (1024**3)
        
        # Allocate 5-10% of RAM to cache, with reasonable bounds
        if memory_gb < 4:
            return 1000
        elif memory_gb < 8:
            return 2500
        elif memory_gb < 16:
            return 5000
        elif memory_gb < 32:
            return 10000
        else:
            return 20000
    
    def _init_disk_cache(self):
        """Initialize the persistent SQLite cache"""
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
                    hit_count INTEGER DEFAULT 1,
                    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create indexes for performance
            conn.execute('CREATE INDEX IF NOT EXISTS idx_hash ON translations(text_hash)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_langs ON translations(source_lang, target_lang)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_accessed ON translations(last_accessed)')
            
            conn.commit()
    
    def _get_text_hash(self, text: str, source_lang: str, target_lang: str) -> str:
        """Generate v1 hash for text + language pair (backward compat)"""
        import re
        normalized = re.sub(r'\s+', ' ', text.strip()).lower()
        content = f"{normalized}|{source_lang}|{target_lang}"
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    def _get_text_hash_v2(self, text: str, source_lang: str, target_lang: str,
                          prev_line: str = "", next_line: str = "") -> str:
        """Generate v2 contextual hash."""
        import re
        normalized = re.sub(r'\s+', ' ', text.strip()).lower()
        prev_norm = re.sub(r'\s+', ' ', prev_line.strip()).lower() if prev_line else ""
        next_norm = re.sub(r'\s+', ' ', next_line.strip()).lower() if next_line else ""
        content = f"{normalized}|{prev_norm}|{next_norm}|{source_lang}|{target_lang}|v2"
        return hashlib.md5(content.encode('utf-8')).hexdigest()
    
    def _update_access_order(self, text_hash: str):
        """Update LRU access order"""
        if text_hash in self.access_order:
            self.access_order.remove(text_hash)
        self.access_order.append(text_hash)
        
        # Trim access order if too large
        if len(self.access_order) > self.memory_cache_size * 2:
            self.access_order = self.access_order[-self.memory_cache_size:]
    
    def _evict_lru_entries(self):
        """Evict least recently used entries from memory cache"""
        while len(self.memory_cache) > self.memory_cache_size:
            if self.access_order:
                lru_hash = self.access_order.pop(0)
                if lru_hash in self.memory_cache:
                    del self.memory_cache[lru_hash]
    
    def get(self, text: str, source_lang: str = 'en', target_lang: str = 'pt-BR',
            prev_line: str = "", next_line: str = "") -> Optional[str]:
        """Get cached translation with dual-read (v2→v1) and LRU update."""
        if not text or len(text.strip()) < 3:
            return None

        # Try v2 hash first (contextual), then v1 (backward compat)
        hash_v2 = self._get_text_hash_v2(text, source_lang, target_lang, prev_line, next_line)
        hash_v1 = self._get_text_hash(text, source_lang, target_lang)
        hashes_to_try = [hash_v2, hash_v1]

        with self.lock:
            for text_hash in hashes_to_try:
                # Check memory cache first (fastest)
                if text_hash in self.memory_cache:
                    entry = self.memory_cache[text_hash]
                    entry.hit_count += 1
                    entry.last_accessed = time.time()
                    self._update_access_order(text_hash)
                    self.memory_hits += 1

                    if self.logger:
                        self.logger.log('debug', f'💾 Cache MEMÓRIA HIT | {text[:50]}...')

                    # Se encontrou em v1 mas não em v2, promover
                    if text_hash == hash_v1 and hash_v2 not in self.memory_cache:
                        self._promote_to_v2(hash_v2, entry)

                    return entry.translated_text

                # Check disk cache (slower but persistent)
                try:
                    with sqlite3.connect(self.cache_file) as conn:
                        cursor = conn.execute(
                            '''SELECT original_text, translated_text, hit_count 
                               FROM translations WHERE text_hash = ?''',
                            (text_hash,)
                        )
                        row = cursor.fetchone()

                        if row:
                            original_text, translated_text, hit_count = row

                            entry = CacheEntry(
                                text_hash=text_hash,
                                original_text=original_text,
                                translated_text=translated_text,
                                source_lang=source_lang,
                                target_lang=target_lang,
                                api_used='cached',
                                created_at=time.time(),
                                hit_count=hit_count + 1,
                                last_accessed=time.time()
                            )

                            self.memory_cache[text_hash] = entry
                            self._update_access_order(text_hash)
                            self._evict_lru_entries()

                            conn.execute(
                                '''UPDATE translations 
                                   SET hit_count = hit_count + 1, last_accessed = CURRENT_TIMESTAMP 
                                   WHERE text_hash = ?''',
                                (text_hash,)
                            )
                            conn.commit()
                            self.disk_hits += 1

                            if self.logger:
                                self.logger.log('debug', f'💾 Cache DISCO HIT | {text[:50]}...')

                            # Promover v1→v2 se necessário
                            if text_hash == hash_v1 and hash_v2 != hash_v1:
                                self._promote_to_v2(hash_v2, entry)

                            return translated_text
                except Exception as e:
                    if self.logger:
                        self.logger.log('error', f'Erro ao consultar cache em disco: {e}')

            self.disk_misses += 1
            self.memory_misses += 1
            return None

    def _promote_to_v2(self, hash_v2: str, entry: CacheEntry):
        """Promove uma entrada v1 para v2 no cache."""
        v2_entry = CacheEntry(
            text_hash=hash_v2,
            original_text=entry.original_text,
            translated_text=entry.translated_text,
            source_lang=entry.source_lang,
            target_lang=entry.target_lang,
            api_used='v1_promoted',
            created_at=time.time(),
            hit_count=1,
            last_accessed=time.time()
        )
        self.memory_cache[hash_v2] = v2_entry
        self._update_access_order(hash_v2)
        try:
            with sqlite3.connect(self.cache_file) as conn:
                conn.execute('''
                    INSERT OR IGNORE INTO translations 
                    (text_hash, original_text, translated_text, source_lang, target_lang, api_used, hit_count, last_accessed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (hash_v2, entry.original_text, entry.translated_text,
                      entry.source_lang, entry.target_lang, 'v1_promoted', 1))
                conn.commit()
        except Exception as e:
            if self.logger:
                self.logger.log('debug', f'Erro ao promover entrada v1 para disco: {e}')
    
    def set(self, original_text: str, translated_text: str, 
            source_lang: str = 'en', target_lang: str = 'pt-BR', api_used: str = 'unknown',
            prev_line: str = "", next_line: str = ""):
        """Cache a translation in both memory and disk (v1 + v2 hashes)."""
        if not original_text or not translated_text or len(original_text.strip()) < 3:
            return
        
        # Don't cache if translation didn't change
        if original_text.strip() == translated_text.strip():
            return
        
        hash_v1 = self._get_text_hash(original_text, source_lang, target_lang)
        hash_v2 = self._get_text_hash_v2(original_text, source_lang, target_lang, prev_line, next_line)
        
        hashes = [hash_v1]
        if hash_v2 != hash_v1:
            hashes.append(hash_v2)
        
        with self.lock:
            for text_hash in hashes:
                entry = CacheEntry(
                    text_hash=text_hash,
                    original_text=original_text.strip(),
                    translated_text=translated_text.strip(),
                    source_lang=source_lang,
                    target_lang=target_lang,
                    api_used=api_used,
                    created_at=time.time(),
                    hit_count=1,
                    last_accessed=time.time()
                )
                
                self.memory_cache[text_hash] = entry
                self._update_access_order(text_hash)
            
            self._evict_lru_entries()
            
            # Update disk cache
            try:
                with sqlite3.connect(self.cache_file) as conn:
                    for text_hash in hashes:
                        conn.execute('''
                            INSERT OR REPLACE INTO translations 
                            (text_hash, original_text, translated_text, source_lang, target_lang, api_used, hit_count, last_accessed)
                            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ''', (text_hash, original_text.strip(), translated_text.strip(),
                              source_lang, target_lang, api_used, 1))
                    conn.commit()
                    
            except Exception as e:
                if self.logger:
                    self.logger.log('error', f'Erro ao salvar cache em disco: {e}')
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive cache statistics"""
        memory_hit_rate = (self.memory_hits / max(1, self.memory_hits + self.memory_misses)) * 100
        disk_hit_rate = (self.disk_hits / max(1, self.disk_hits + self.disk_misses)) * 100
        total_hits = self.memory_hits + self.disk_hits
        total_requests = total_hits + self.memory_misses + self.disk_misses
        overall_hit_rate = (total_hits / max(1, total_requests)) * 100
        
        return {
            'memory_cache': {
                'size': len(self.memory_cache),
                'max_size': self.memory_cache_size,
                'utilization': f"{(len(self.memory_cache) / self.memory_cache_size * 100):.1f}%",
                'hits': self.memory_hits,
                'misses': self.memory_misses,
                'hit_rate': f"{memory_hit_rate:.1f}%"
            },
            'disk_cache': {
                'hits': self.disk_hits,
                'misses': self.disk_misses,
                'hit_rate': f"{disk_hit_rate:.1f}%"
            },
            'overall': {
                'total_requests': total_requests,
                'total_hits': total_hits,
                'total_misses': self.memory_misses + self.disk_misses,
                'overall_hit_rate': f"{overall_hit_rate:.1f}%"
            }
        }
    
    def cleanup_old_entries(self, days: int = 30):
        """Remove old cache entries from disk"""
        cutoff_date = datetime.now() - timedelta(days=days)
        
        with self.lock:
            try:
                with sqlite3.connect(self.cache_file) as conn:
                    cursor = conn.execute(
                        'DELETE FROM translations WHERE created_at < ?',
                        (cutoff_date,)
                    )
                    deleted_count = cursor.rowcount
                    conn.commit()
                    
                    if self.logger:
                        self.logger.log('info', f'🧹 Limpeza de cache: {deleted_count} entradas antigas removidas')
                    
                    return deleted_count
                    
            except Exception as e:
                if self.logger:
                    self.logger.log('error', f'Erro ao limpar cache antigo: {e}')
                return 0
    
    def cleanup_bad_translations(self) -> int:
        """Remove cached translations that are identical to original (non-translations)"""
        with self.lock:
            try:
                with sqlite3.connect(self.cache_file) as conn:
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
                        
                        # Remove from memory cache too
                        for text_hash, _, _ in bad_entries:
                            if text_hash in self.memory_cache:
                                del self.memory_cache[text_hash]
                                if text_hash in self.access_order:
                                    self.access_order.remove(text_hash)
                        
                        if self.logger:
                            self.logger.log('info', f'🧹 Limpeza de traduções ruins: {len(bad_entries)} entradas removidas')
                    
                    return len(bad_entries)
                    
            except Exception as e:
                if self.logger:
                    self.logger.log('error', f'Erro ao limpar traduções ruins: {e}')
                return 0
    
    def clear_memory_cache(self):
        """Clear in-memory cache (keeps disk cache)"""
        with self.lock:
            self.memory_cache.clear()
            self.access_order.clear()
            self.memory_hits = 0
            self.memory_misses = 0
            
            if self.logger:
                self.logger.log('info', '🧹 Cache em memória limpo')
    
    def clear_disk_cache(self):
        """Clear persistent disk cache"""
        with self.lock:
            try:
                with sqlite3.connect(self.cache_file) as conn:
                    conn.execute('DELETE FROM translations')
                    conn.commit()
                    
                self.clear_memory_cache()
                
                if self.logger:
                    self.logger.log('info', '🧹 Cache em disco limpo')
                    
            except Exception as e:
                if self.logger:
                    self.logger.log('error', f'Erro ao limpar cache em disco: {e}')
    
    def optimize_cache(self):
        """Optimize cache performance"""
        with self.lock:
            # Clean up bad translations
            bad_count = self.cleanup_bad_translations()
            
            # Clean up old entries
            old_count = self.cleanup_old_entries(days=30)
            
            # Vacuum database for better performance
            try:
                with sqlite3.connect(self.cache_file) as conn:
                    conn.execute('VACUUM')
                    conn.commit()
            except Exception as e:
                if self.logger:
                    self.logger.log('warning', f'AVISO: Não foi possível otimizar banco de dados: {e}')
            
            if self.logger:
                self.logger.log('info', f'🔧 Otimização de cache concluída: {bad_count} ruins + {old_count} antigos removidos')
    
    def get_memory_usage(self) -> Dict[str, Any]:
        """Get memory usage statistics"""
        total_size = 0
        for entry in self.memory_cache.values():
            # Estimate memory usage (rough calculation)
            total_size += len(entry.original_text) + len(entry.translated_text) + 200  # overhead
        
        memory_info = psutil.Process().memory_info()
        
        return {
            'entries_count': len(self.memory_cache),
            'estimated_size_bytes': total_size,
            'estimated_size_mb': total_size / (1024 * 1024),
            'process_memory_mb': memory_info.rss / (1024 * 1024),
            'cache_percentage': (total_size / max(1, memory_info.rss)) * 100
        }

# Global cache instance for reuse
_global_cache = None
_cache_lock = threading.Lock()

def get_global_cache(cache_file='translation_cache.db', logger=None) -> HybridCache:
    """Get or create global cache instance"""
    global _global_cache
    
    if _global_cache is None:
        with _cache_lock:
            if _global_cache is None:
                _global_cache = HybridCache(cache_file, logger)
    
    return _global_cache

def cleanup_global_cache():
    """Cleanup global cache resources"""
    global _global_cache
    
    with _cache_lock:
        if _global_cache:
            _global_cache = None