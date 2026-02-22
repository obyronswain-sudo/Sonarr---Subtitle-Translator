import threading
import time

class ResourceMonitor:
    def __init__(self, logger):
        self.logger = logger
        self.monitoring = False
        self.monitor_thread = None
        
    def start_monitoring(self):
        if not self.monitoring:
            self.monitoring = True
            self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.monitor_thread.start()
            self.logger.log('info', 'Monitor de recursos iniciado')
    
    def stop_monitoring(self):
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=1)
        self.logger.log('info', 'Monitor de recursos parado')
    
    def _monitor_loop(self):
        while self.monitoring:
            try:
                # Simple thread count monitoring
                active_threads = threading.active_count()
                
                # Log only if thread count is high
                if active_threads > 10:
                    self.logger.log('warning', f'Muitas threads ativas: {active_threads}')
                
                time.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                self.logger.log('error', f'Erro no monitor de recursos: {e}')
                break
    
    def get_current_usage(self):
        try:
            return {
                'active_threads': threading.active_count()
            }
        except:
            return None