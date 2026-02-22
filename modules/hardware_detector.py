"""
Hardware detection and Ollama model recommendation system
"""
try:
    import psutil
except ImportError:
    psutil = None

import platform
import subprocess
import requests
from typing import Dict, List, Tuple

class HardwareDetector:
    """Detect system hardware and recommend Ollama models"""
    
    # Model recommendations based on VRAM
    MODELS = {
        '8b_q6': {
            'name': 'qwen2.5:32b-instruct-q4_K_M',
            'vram_required': 6,
            'description': 'Qwen3 8B Q6_K (recomendado, 6GB VRAM)',
            'performance': 'Fast',
            'quality': 'Very Good'
        },
        '7b': {
            'name': 'qwen2.5:7b-instruct-q5_K_M',
            'vram_required': 4,
            'description': '7B - Fast & Lightweight (4GB VRAM)',
            'performance': 'Fast',
            'quality': 'Good'
        },
        '14b': {
            'name': 'qwen2.5:14b-instruct-q4_K_M',
            'vram_required': 8,
            'description': '14B - Balanced (8GB VRAM)',
            'performance': 'Medium',
            'quality': 'Very Good'
        },
        '32b': {
            'name': 'qwen2.5:32b-instruct-q4_K_M',
            'vram_required': 12,
            'description': '32B - High Quality (12GB+ VRAM)',
            'performance': 'Slower',
            'quality': 'Excellent'
        },
    }
    
    def __init__(self):
        self.ram_gb = self._get_ram()
        self.vram_gb = self._get_vram()
        self.cpu_cores = self._get_cpu_cores()
        self.os_name = platform.system()
    
    def _get_ram(self) -> int:
        """Get total system RAM in GB"""
        if not psutil:
            return 8  # Default fallback if psutil not available
        try:
            return int(psutil.virtual_memory().total / (1024**3))
        except:
            return 8  # Default fallback
    
    def _get_vram(self) -> int:
        """Get available VRAM in GB (GPU memory)"""
        try:
            # Try NVIDIA
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=memory.total', '--format=csv,nounits,noheader'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                vram_mb = int(result.stdout.strip().split('\n')[0])
                return int(vram_mb / 1024)
        except:
            pass
        
        try:
            # Try AMD
            result = subprocess.run(
                ['rocm-smi', '--showproductname'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return 8  # Default for AMD
        except:
            pass
        
        return 0  # No dedicated GPU
    
    def _get_cpu_cores(self) -> int:
        """Get number of CPU cores"""
        try:
            return psutil.cpu_count(logical=False) or 4
        except:
            return 4
    
    def get_recommended_model(self) -> str:
        """Get recommended model based on hardware"""
        # Prioritize VRAM if GPU available (Qwen3 8B Q6_K como padrão recomendado)
        if self.vram_gb >= 12:
            return self.MODELS['32b']['name']
        elif self.vram_gb >= 8:
            return self.MODELS['14b']['name']
        elif self.vram_gb >= 6:
            return self.MODELS['8b_q6']['name']
        elif self.vram_gb >= 4:
            return self.MODELS['7b']['name']
        
        # Fall back to RAM if no GPU
        if self.ram_gb >= 20:
            return self.MODELS['32b']['name']
        elif self.ram_gb >= 16:
            return self.MODELS['14b']['name']
        elif self.ram_gb >= 8:
            return self.MODELS['8b_q6']['name']
        else:
            return self.MODELS['7b']['name']
    
    def get_model_list(self) -> List[Dict]:
        """Get list of all available models (Qwen3 8B Q6_K primeiro como recomendado)"""
        return [
            {
                'key': '8b_q6',
                'name': self.MODELS['8b_q6']['name'],
                'display': self.MODELS['8b_q6']['description'],
                'vram': self.MODELS['8b_q6']['vram_required']
            },
            {
                'key': '7b',
                'name': self.MODELS['7b']['name'],
                'display': self.MODELS['7b']['description'],
                'vram': self.MODELS['7b']['vram_required']
            },
            {
                'key': '14b',
                'name': self.MODELS['14b']['name'],
                'display': self.MODELS['14b']['description'],
                'vram': self.MODELS['14b']['vram_required']
            },
            {
                'key': '32b',
                'name': self.MODELS['32b']['name'],
                'display': self.MODELS['32b']['description'],
                'vram': self.MODELS['32b']['vram_required']
            },
        ]
    
    def get_hardware_info(self) -> Dict:
        """Get complete hardware information"""
        return {
            'ram_gb': self.ram_gb,
            'vram_gb': self.vram_gb,
            'cpu_cores': self.cpu_cores,
            'os': self.os_name,
            'has_gpu': self.vram_gb > 0,
            'recommended_model': self.get_recommended_model()
        }
    
    def is_model_available(self, ollama_url: str, model_name: str) -> bool:
        """Check if model is available in Ollama"""
        try:
            response = requests.get(f"{ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                models = response.json().get('models', [])
                model_names = [m.get('name', '') for m in models]
                return any(model_name in name for name in model_names)
        except:
            pass
        return False
    
    def pull_model(self, ollama_url: str, model_name: str, progress_callback=None) -> bool:
        """Download a model from Ollama"""
        try:
            url = f"{ollama_url}/api/pull"
            payload = {"name": model_name, "stream": False}
            
            response = requests.post(url, json=payload, timeout=3600)  # 1 hour timeout for download
            
            if response.status_code == 200:
                if progress_callback:
                    progress_callback(100, f"✅ Model {model_name} downloaded successfully")
                return True
            else:
                if progress_callback:
                    progress_callback(0, f"❌ Failed to pull model: {response.status_code}")
                return False
        except Exception as e:
            if progress_callback:
                progress_callback(0, f"❌ Error pulling model: {str(e)}")
            return False
