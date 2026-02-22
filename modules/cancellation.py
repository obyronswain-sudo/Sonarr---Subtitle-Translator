"""
Graceful shutdown and cancellation system
"""
import threading
import time
from typing import Callable, Optional

class CancellationManager:
    def __init__(self):
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self.callbacks = []
        self.active_threads = set()
        self.lock = threading.Lock()
    
    def register_thread(self, thread_id=None):
        """Register a thread for tracking"""
        if thread_id is None:
            thread_id = threading.current_thread().ident
        
        with self.lock:
            self.active_threads.add(thread_id)
    
    def unregister_thread(self, thread_id=None):
        """Unregister a thread"""
        if thread_id is None:
            thread_id = threading.current_thread().ident
        
        with self.lock:
            self.active_threads.discard(thread_id)
    
    def add_cancel_callback(self, callback: Callable):
        """Add callback to be called on cancellation"""
        self.callbacks.append(callback)
    
    def request_cancel(self):
        """Request cancellation of all operations"""
        self.cancel_event.set()
        
        # Call all registered callbacks
        for callback in self.callbacks:
            try:
                callback()
            except Exception:
                pass  # Ignore callback errors during shutdown
    
    def request_pause(self):
        """Request pause of all operations"""
        self.pause_event.set()
    
    def resume(self):
        """Resume paused operations"""
        self.pause_event.clear()
    
    def reset(self):
        """Reset cancellation state"""
        self.cancel_event.clear()
        self.pause_event.clear()
    
    def is_cancelled(self):
        """Check if cancellation was requested"""
        return self.cancel_event.is_set()
    
    def is_paused(self):
        """Check if pause was requested"""
        return self.pause_event.is_set()
    
    def check_cancellation(self, raise_exception=False):
        """Check for cancellation and optionally raise exception"""
        if self.is_cancelled():
            if raise_exception:
                raise CancellationException("Operation was cancelled")
            return True
        return False
    
    def wait_if_paused(self, timeout=None):
        """Wait if paused, return True if resumed, False if cancelled"""
        if self.is_paused() and not self.is_cancelled():
            # Wait for either resume or cancel
            events = [self.pause_event, self.cancel_event]
            # Wait until pause is cleared or cancel is set
            while self.pause_event.is_set() and not self.cancel_event.is_set():
                time.sleep(0.1)
                if timeout and timeout <= 0:
                    break
                if timeout:
                    timeout -= 0.1
        
        return not self.is_cancelled()
    
    def sleep_interruptible(self, duration):
        """Sleep that can be interrupted by cancellation"""
        end_time = time.time() + duration
        while time.time() < end_time:
            if self.is_cancelled():
                return False
            
            if self.is_paused():
                if not self.wait_if_paused():
                    return False
            
            time.sleep(min(0.1, end_time - time.time()))
        
        return True
    
    def wait_for_threads(self, timeout=30):
        """Wait for all registered threads to finish"""
        start_time = time.time()
        
        while self.active_threads and (time.time() - start_time) < timeout:
            time.sleep(0.1)
        
        return len(self.active_threads) == 0
    
    def get_active_thread_count(self):
        """Get number of active threads"""
        with self.lock:
            return len(self.active_threads)

class CancellationException(Exception):
    """Exception raised when operation is cancelled"""
    pass

class CancellableOperation:
    """Context manager for cancellable operations"""
    
    def __init__(self, cancel_manager: CancellationManager, operation_name: str = "operation"):
        self.cancel_manager = cancel_manager
        self.operation_name = operation_name
        self.thread_id = None
    
    def __enter__(self):
        self.thread_id = threading.current_thread().ident
        self.cancel_manager.register_thread(self.thread_id)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.thread_id:
            self.cancel_manager.unregister_thread(self.thread_id)
    
    def check_cancelled(self):
        """Check if operation should be cancelled"""
        return self.cancel_manager.check_cancellation()
    
    def sleep(self, duration):
        """Cancellable sleep"""
        return self.cancel_manager.sleep_interruptible(duration)
    
    def wait_if_paused(self):
        """Wait if operation is paused"""
        return self.cancel_manager.wait_if_paused()