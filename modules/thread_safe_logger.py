"""
Thread-safe logger implementation using queue handler
"""
import logging
import logging.handlers
import queue
import threading
from pathlib import Path
import time

class ThreadSafeLogger:
    def __init__(self, log_file='subtitle_translator.log', max_size=10*1024*1024, backup_count=5):
        self.log_file = Path(log_file)
        self.queue = queue.Queue()
        self.handler_thread = None
        self.stop_event = threading.Event()
        
        # Setup logging
        self.logger = logging.getLogger('SubtitleTranslator')
        self.logger.setLevel(logging.DEBUG)
        
        # Clear existing handlers
        self.logger.handlers.clear()
        
        # Create queue handler
        queue_handler = logging.handlers.QueueHandler(self.queue)
        self.logger.addHandler(queue_handler)
        
        # Start handler thread
        self._start_handler_thread(max_size, backup_count)
    
    def _start_handler_thread(self, max_size, backup_count):
        """Start the logging handler thread"""
        def handler_worker():
            # Create file handler with rotation
            file_handler = logging.handlers.RotatingFileHandler(
                self.log_file, 
                maxBytes=max_size, 
                backupCount=backup_count,
                encoding='utf-8'
            )
            
            # Create console handler
            console_handler = logging.StreamHandler()
            
            # Create formatter
            formatter = logging.Formatter(
                '%(asctime)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            
            file_handler.setFormatter(formatter)
            console_handler.setFormatter(formatter)
            
            # Process queue messages
            while not self.stop_event.is_set():
                try:
                    record = self.queue.get(timeout=1)
                    if record is None:  # Sentinel to stop
                        break
                    
                    # Write to file and console
                    file_handler.emit(record)
                    console_handler.emit(record)
                    
                except queue.Empty:
                    continue
                except Exception as e:
                    # Fallback logging to stderr
                    import sys
                    print(f"Logger error: {e}", file=sys.stderr)
            
            # Cleanup
            file_handler.close()
            console_handler.close()
        
        self.handler_thread = threading.Thread(target=handler_worker, daemon=True)
        self.handler_thread.start()
    
    def log(self, level, message):
        """Log a message with specified level"""
        level_map = {
            'debug': logging.DEBUG,
            'info': logging.INFO,
            'warning': logging.WARNING,
            'error': logging.ERROR,
            'critical': logging.CRITICAL
        }
        
        log_level = level_map.get(level.lower(), logging.INFO)
        self.logger.log(log_level, message)
    
    def debug(self, message):
        self.log('debug', message)
    
    def info(self, message):
        self.log('info', message)
    
    def warning(self, message):
        self.log('warning', message)
    
    def error(self, message):
        self.log('error', message)
    
    def critical(self, message):
        self.log('critical', message)
    
    def close(self):
        """Close the logger and cleanup"""
        self.stop_event.set()
        
        # Send sentinel to stop handler thread
        self.queue.put(None)
        
        # Wait for handler thread to finish
        if self.handler_thread and self.handler_thread.is_alive():
            self.handler_thread.join(timeout=5)
    
    def __del__(self):
        """Cleanup on destruction"""
        try:
            self.close()
        except:
            pass