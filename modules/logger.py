import logging
import os
from datetime import datetime

class Logger:
    def __init__(self, log_file='app.log', callback=None, error_callback=None):
        self.log_file = log_file
        self.callback = callback
        self.error_callback = error_callback
        self.setup_logger()

    def setup_logger(self):
        # Remove all existing handlers to avoid duplicates
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        
        # Setup file handler with UTF-8 encoding
        file_handler = logging.FileHandler(self.log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        
        # Setup logger
        self.logger = logging.getLogger()
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(file_handler)

    def log(self, level, message):
        if level == 'debug':
            self.logger.debug(message)
        elif level == 'info':
            self.logger.info(message)
        elif level == 'warning':
            self.logger.warning(message)
        elif level == 'error':
            self.logger.error(message)
            if self.error_callback:
                self.error_callback(message)
        elif level == 'critical':
            self.logger.critical(message)
            if self.error_callback:
                self.error_callback(message)
        if self.callback:
            self.callback(message)

    def get_logs(self):
        if os.path.exists(self.log_file):
            with open(self.log_file, 'r', encoding='utf-8') as f:
                return f.read()
        return ""