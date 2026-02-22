"""
Automatic dependency installer for missing packages
"""
import subprocess
import sys
import importlib
from pathlib import Path

class DependencyInstaller:
    def __init__(self, logger=None):
        self.logger = logger
        
        # Essential dependencies with fallback versions
        self.essential_deps = {
            'PySide6': 'PySide6>=6.5.0',
            'requests': 'requests>=2.31.0', 
            'langdetect': 'langdetect>=1.0.9',
            'chardet': 'chardet>=5.0.0',
            'tenacity': 'tenacity>=8.2.0',
            'dotenv': 'python-dotenv>=1.0.0',
            'qdarkstyle': 'qdarkstyle>=3.2.0'
        }
        
        # Optional dependencies
        self.optional_deps = {
            'deepl': 'deepl>=1.15.0',
            'openai': 'openai>=1.0.0',
            'google.generativeai': 'google-generativeai>=0.3.0',
            # googletrans uses unofficial Google API reverse-engineering and may break at any time.
            # Pinned to the last known working pre-release.
            'googletrans': 'googletrans==4.0.0rc1',
            'flask': 'flask>=2.3.0',
            'libretranslate': 'libretranslate>=1.3.11'
        }
    
    def check_and_install_essentials(self):
        """Check and install essential dependencies"""
        missing_deps = []
        
        for module_name, pip_name in self.essential_deps.items():
            if not self._is_module_available(module_name):
                missing_deps.append(pip_name)
        
        if missing_deps:
            if self.logger:
                self.logger.log('info', f'📦 Instalando dependências essenciais: {", ".join(missing_deps)}')
            
            success = self._install_packages(missing_deps)
            if not success:
                raise RuntimeError("Falha ao instalar dependências essenciais")
        
        return True
    
    def check_and_install_optional(self, requested_apis=None):
        """Check and install optional dependencies for requested APIs"""
        if not requested_apis:
            return True
        
        api_deps = {
            'DeepL': 'deepl',
            'GPT': 'openai', 
            'Gemini': 'google.generativeai',
            'Google': 'googletrans',
            'LibreTranslate': 'libretranslate'
        }
        
        missing_deps = []
        for api in requested_apis:
            if api in api_deps:
                module_name = api_deps[api]
                if not self._is_module_available(module_name):
                    if module_name in self.optional_deps:
                        missing_deps.append(self.optional_deps[module_name])
        
        if missing_deps:
            if self.logger:
                self.logger.log('info', f'📦 Instalando APIs solicitadas: {", ".join(missing_deps)}')
            
            return self._install_packages(missing_deps)
        
        return True
    
    def install_from_requirements(self, requirements_file='requirements_minimal.txt'):
        """Install all dependencies from requirements file"""
        req_file = Path(requirements_file)
        if not req_file.exists():
            if self.logger:
                self.logger.log('warning', f'Arquivo {requirements_file} não encontrado')
            return False
        
        if self.logger:
            self.logger.log('info', f'📦 Instalando dependências de {requirements_file}')
        
        try:
            result = subprocess.run([
                sys.executable, '-m', 'pip', 'install', '-r', str(req_file)
            ], capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0:
                if self.logger:
                    self.logger.log('info', '✅ Todas as dependências instaladas com sucesso')
                return True
            else:
                if self.logger:
                    self.logger.log('error', f'❌ Erro na instalação: {result.stderr}')
                return False
                
        except subprocess.TimeoutExpired:
            if self.logger:
                self.logger.log('error', '❌ Timeout na instalação de dependências')
            return False
        except Exception as e:
            if self.logger:
                self.logger.log('error', f'❌ Erro inesperado: {e}')
            return False
    
    def _is_module_available(self, module_name):
        """Check if a module is available for import"""
        try:
            importlib.import_module(module_name)
            return True
        except ImportError:
            return False
    
    def _install_packages(self, packages):
        """Install list of packages using pip"""
        try:
            cmd = [sys.executable, '-m', 'pip', 'install'] + packages
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0:
                if self.logger:
                    self.logger.log('info', '✅ Pacotes instalados com sucesso')
                return True
            else:
                if self.logger:
                    self.logger.log('error', f'❌ Erro na instalação: {result.stderr}')
                return False
                
        except subprocess.TimeoutExpired:
            if self.logger:
                self.logger.log('error', '❌ Timeout na instalação')
            return False
        except Exception as e:
            if self.logger:
                self.logger.log('error', f'❌ Erro na instalação: {e}')
            return False
    
    def get_missing_essentials(self):
        """Get list of missing essential dependencies"""
        missing = []
        for module_name, pip_name in self.essential_deps.items():
            if not self._is_module_available(module_name):
                missing.append(pip_name)
        return missing
    
    def get_available_apis(self):
        """Get list of available translation APIs based on installed packages"""
        available = ['Ollama']  # Always available if requests is installed
        
        api_modules = {
            'DeepL': 'deepl',
            'GPT': 'openai',
            'Gemini': 'google.generativeai', 
            'Google': 'googletrans',
            'LibreTranslate': 'libretranslate'
        }
        
        for api, module in api_modules.items():
            if self._is_module_available(module):
                available.append(api)
        
        return available