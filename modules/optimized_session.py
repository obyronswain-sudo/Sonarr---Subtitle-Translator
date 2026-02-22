"""
Optimized HTTP Session Pool for Translation APIs
Provides connection pooling, retry logic, and performance monitoring
"""
import requests
import time
import threading
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Optional, Dict, Any, Union
from dataclasses import dataclass
from datetime import datetime
import json

@dataclass
class RequestStats:
    """Statistics for request performance monitoring"""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_response_time: float = 0.0
    min_response_time: float = float('inf')
    max_response_time: float = 0.0
    avg_response_time: float = 0.0
    last_error: Optional[str] = None

class OptimizedSession:
    """
    High-performance HTTP session with:
    - Connection pooling
    - Automatic retry logic
    - Performance monitoring
    - Request/response logging
    """
    
    def __init__(self, base_url: str = None, logger=None, timeout: int = 30):
        self.base_url = base_url
        self.logger = logger
        self.timeout = timeout
        self.session = None
        self.stats = RequestStats()
        self.lock = threading.RLock()
        
        # Performance thresholds
        self.slow_request_threshold = 5.0  # seconds
        self.error_threshold = 0.1  # 10% error rate
        
        self._init_session()
        
        if self.logger:
            self.logger.log('info', f'✅ Session pool HTTP iniciado (Timeout: {timeout}s)')
    
    def _init_session(self):
        """Initialize the optimized HTTP session"""
        self.session = requests.Session()
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS"]
        )
        
        # Configure HTTP adapter with connection pooling
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,  # Number of connection pools
            pool_maxsize=20,      # Max connections per pool
            pool_block=True       # Block when pool is full
        )
        
        # Mount adapters for both HTTP and HTTPS
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Set default headers
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'SonarrSubtitleTranslator/2.0',
            'Accept': 'application/json'
        })
    
    def _update_stats(self, response_time: float, success: bool, error_msg: Optional[str] = None):
        """Update request statistics"""
        with self.lock:
            self.stats.total_requests += 1
            
            if success:
                self.stats.successful_requests += 1
                self.stats.total_response_time += response_time
                self.stats.min_response_time = min(self.stats.min_response_time, response_time)
                self.stats.max_response_time = max(self.stats.max_response_time, response_time)
                self.stats.avg_response_time = self.stats.total_response_time / self.stats.successful_requests
            else:
                self.stats.failed_requests += 1
                self.stats.last_error = error_msg
    
    def _log_request(self, method: str, url: str, response_time: float, success: bool, 
                    status_code: Optional[int] = None, error_msg: Optional[str] = None):
        """Log request details"""
        if not self.logger:
            return
        
        level = 'info' if success else 'error'
        status_info = f"Status: {status_code}" if status_code else f"Error: {error_msg}"
        
        self.logger.log(level, 
                       f'🌐 {method} {url} | {response_time:.2f}s | {status_info}')
    
    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        """
        Make an HTTP request with monitoring and retry logic
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: Target URL
            **kwargs: Additional request parameters
        
        Returns:
            requests.Response object
        """
        # Build full URL if base_url is provided
        if self.base_url and not url.startswith(('http://', 'https://')):
            url = f"{self.base_url.rstrip('/')}/{url.lstrip('/')}"
        
        start_time = time.time()
        success = False
        status_code = None
        error_msg = None
        
        try:
            # Make the request
            response = self.session.request(
                method=method,
                url=url,
                timeout=self.timeout,
                **kwargs
            )
            
            response_time = time.time() - start_time
            status_code = response.status_code
            
            # Check for successful response
            success = response.status_code < 400
            
            # Log slow requests
            if response_time > self.slow_request_threshold:
                if self.logger:
                    self.logger.log('warning', 
                                   f'⚠️ Requisição lenta detectada: {response_time:.2f}s > {self.slow_request_threshold}s')
            
            # Log response details
            self._log_request(method, url, response_time, success, status_code)
            
            # Update statistics
            self._update_stats(response_time, success)
            
            return response
            
        except requests.exceptions.Timeout as e:
            error_msg = f"Timeout after {self.timeout}s"
            self._log_request(method, url, time.time() - start_time, False, error_msg=error_msg)
            self._update_stats(time.time() - start_time, False, error_msg)
            raise
            
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Connection error: {str(e)}"
            self._log_request(method, url, time.time() - start_time, False, error_msg=error_msg)
            self._update_stats(time.time() - start_time, False, error_msg)
            raise
            
        except requests.exceptions.RequestException as e:
            error_msg = f"Request failed: {str(e)}"
            self._log_request(method, url, time.time() - start_time, False, error_msg=error_msg)
            self._update_stats(time.time() - start_time, False, error_msg)
            raise
    
    def get(self, url: str, **kwargs) -> requests.Response:
        """Make a GET request"""
        return self.request('GET', url, **kwargs)
    
    def post(self, url: str, **kwargs) -> requests.Response:
        """Make a POST request"""
        return self.request('POST', url, **kwargs)
    
    def put(self, url: str, **kwargs) -> requests.Response:
        """Make a PUT request"""
        return self.request('PUT', url, **kwargs)
    
    def delete(self, url: str, **kwargs) -> requests.Response:
        """Make a DELETE request"""
        return self.request('DELETE', url, **kwargs)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive session statistics"""
        with self.lock:
            error_rate = (self.stats.failed_requests / max(1, self.stats.total_requests)) * 100
            success_rate = (self.stats.successful_requests / max(1, self.stats.total_requests)) * 100
            
            return {
                'session_info': {
                    'base_url': self.base_url,
                    'timeout': self.timeout,
                    'pool_connections': 10,
                    'pool_maxsize': 20
                },
                'performance': {
                    'total_requests': self.stats.total_requests,
                    'successful_requests': self.stats.successful_requests,
                    'failed_requests': self.stats.failed_requests,
                    'success_rate': f"{success_rate:.1f}%",
                    'error_rate': f"{error_rate:.1f}%"
                },
                'timing': {
                    'avg_response_time': f"{self.stats.avg_response_time:.2f}s",
                    'min_response_time': f"{self.stats.min_response_time:.2f}s",
                    'max_response_time': f"{self.stats.max_response_time:.2f}s",
                    'slow_request_threshold': f"{self.slow_request_threshold}s"
                },
                'last_error': self.stats.last_error,
                'health_status': self._get_health_status()
            }
    
    def _get_health_status(self) -> str:
        """Get overall health status of the session"""
        with self.lock:
            error_rate = (self.stats.failed_requests / max(1, self.stats.total_requests)) * 100
            
            if self.stats.total_requests == 0:
                return "unknown"
            elif error_rate > 20:
                return "critical"
            elif error_rate > 10:
                return "warning"
            elif self.stats.failed_requests > 0:
                return "degraded"
            else:
                return "healthy"
    
    def check_health(self) -> bool:
        """Check if the session is healthy"""
        stats = self.get_stats()
        return stats['health_status'] in ['healthy', 'degraded']
    
    def reset_stats(self):
        """Reset all statistics"""
        with self.lock:
            self.stats = RequestStats()
            
            if self.logger:
                self.logger.log('info', '📊 Estatísticas de sessão resetadas')
    
    def close(self):
        """Close the session and cleanup resources"""
        if self.session:
            self.session.close()
            
            if self.logger:
                self.logger.log('info', '🛑 Session pool HTTP encerrada')
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

class OllamaSession(OptimizedSession):
    """
    Specialized session for Ollama API with additional features
    """
    
    def __init__(self, base_url: str = "http://localhost:11434", logger=None, timeout: int = 60):
        super().__init__(base_url, logger, timeout)
        
        # Ollama-specific headers
        self.session.headers.update({
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        })
    
    def check_ollama_health(self) -> Dict[str, Any]:
        """Check if Ollama server is healthy"""
        try:
            response = self.get('/api/tags', timeout=10)
            if response.status_code == 200:
                models = response.json().get('models', [])
                return {
                    'status': 'healthy',
                    'models_available': len(models),
                    'models': [model['name'] for model in models[:5]],  # Show first 5 models
                    'response_time': self.get_stats()['timing']['avg_response_time']
                }
            else:
                return {'status': 'unhealthy', 'error': f'HTTP {response.status_code}'}
                
        except Exception as e:
            return {'status': 'error', 'error': str(e)}
    
    def list_models(self) -> Dict[str, Any]:
        """List available models in Ollama"""
        try:
            response = self.get('/api/tags')
            if response.status_code == 200:
                return response.json()
            else:
                return {'error': f'HTTP {response.status_code}', 'message': response.text}
                
        except Exception as e:
            return {'error': str(e)}
    
    def generate_text(self, model: str, prompt: str, system_prompt: str = None, 
                     temperature: float = 0.7, max_tokens: int = 2000) -> Dict[str, Any]:
        """
        Generate text using Ollama
        
        Args:
            model: Model name
            prompt: User prompt
            system_prompt: System prompt (optional)
            temperature: Temperature for randomness
            max_tokens: Maximum tokens to generate
        
        Returns:
            Dict with response or error details
        """
        payload = {
            'model': model,
            'prompt': prompt,
            'stream': False,
            'options': {
                'temperature': temperature,
                'num_predict': max_tokens
            }
        }
        
        if system_prompt:
            payload['system'] = system_prompt
        
        try:
            response = self.post('/api/generate', json=payload)
            
            if response.status_code == 200:
                result = response.json()
                return {
                    'success': True,
                    'response': result.get('response', ''),
                    'model': result.get('model', model),
                    'total_duration': result.get('total_duration', 0),
                    'load_duration': result.get('load_duration', 0)
                }
            else:
                return {
                    'success': False,
                    'error': f'HTTP {response.status_code}',
                    'message': response.text
                }
                
        except Exception as e:
            return {
                'success': False,
                'error': 'Exception',
                'message': str(e)
            }

# Global session instances for reuse
_global_ollama_session = None
_global_http_session = None
_session_lock = threading.Lock()

def get_global_ollama_session(base_url: str = "http://localhost:11434", logger=None) -> OllamaSession:
    """Get or create global Ollama session instance"""
    global _global_ollama_session
    
    if _global_ollama_session is None:
        with _session_lock:
            if _global_ollama_session is None:
                _global_ollama_session = OllamaSession(base_url, logger)
    
    return _global_ollama_session

def get_global_http_session(base_url: str = None, logger=None) -> OptimizedSession:
    """Get or create global HTTP session instance"""
    global _global_http_session
    
    if _global_http_session is None:
        with _session_lock:
            if _global_http_session is None:
                _global_http_session = OptimizedSession(base_url, logger)
    
    return _global_http_session

def cleanup_global_sessions():
    """Cleanup global session resources"""
    global _global_ollama_session, _global_http_session
    
    with _session_lock:
        if _global_ollama_session:
            _global_ollama_session.close()
            _global_ollama_session = None
        
        if _global_http_session:
            _global_http_session.close()
            _global_http_session = None