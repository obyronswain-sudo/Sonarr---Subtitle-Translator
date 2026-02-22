"""
Cross-platform tool detection utilities
"""
import shutil
import os
import platform
from pathlib import Path

class ToolDetector:
    def __init__(self, logger=None):
        self.logger = logger
        self.system = platform.system().lower()
        
        # Common tool paths by platform
        self.tool_paths = {
            'mkvextract': {
                'windows': [
                    'C:\\Program Files\\MKVToolNix\\mkvextract.exe',
                    'C:\\Program Files (x86)\\MKVToolNix\\mkvextract.exe',
                    'mkvextract.exe'
                ],
                'linux': [
                    '/usr/bin/mkvextract',
                    '/usr/local/bin/mkvextract',
                    'mkvextract'
                ],
                'darwin': [  # macOS
                    '/usr/local/bin/mkvextract',
                    '/opt/homebrew/bin/mkvextract',
                    'mkvextract'
                ]
            },
            'ffmpeg': {
                'windows': [
                    'C:\\ffmpeg\\bin\\ffmpeg.exe',
                    'ffmpeg.exe'
                ],
                'linux': [
                    '/usr/bin/ffmpeg',
                    '/usr/local/bin/ffmpeg',
                    'ffmpeg'
                ],
                'darwin': [
                    '/usr/local/bin/ffmpeg',
                    '/opt/homebrew/bin/ffmpeg',
                    'ffmpeg'
                ]
            },
            'tesseract': {
                'windows': [
                    'C:\\Program Files\\Tesseract-OCR\\tesseract.exe',
                    'C:\\Program Files (x86)\\Tesseract-OCR\\tesseract.exe',
                    'tesseract.exe'
                ],
                'linux': [
                    '/usr/bin/tesseract',
                    '/usr/local/bin/tesseract',
                    'tesseract'
                ],
                'darwin': [
                    '/usr/local/bin/tesseract',
                    '/opt/homebrew/bin/tesseract',
                    'tesseract'
                ]
            }
        }
    
    def find_tool(self, tool_name):
        """Find tool executable path using multiple methods"""
        # Method 1: Use shutil.which (checks PATH)
        tool_path = shutil.which(tool_name)
        if tool_path and Path(tool_path).exists():
            if self.logger:
                self.logger.log('info', f'✅ Found {tool_name} in PATH: {tool_path}')
            return tool_path
        
        # Method 2: Check common installation paths
        if tool_name in self.tool_paths and self.system in self.tool_paths[tool_name]:
            for path in self.tool_paths[tool_name][self.system]:
                if Path(path).exists():
                    if self.logger:
                        self.logger.log('info', f'✅ Found {tool_name} at: {path}')
                    return path
        
        # Method 3: Environment variable override
        env_var = f'{tool_name.upper()}_PATH'
        env_path = os.environ.get(env_var)
        if env_path and Path(env_path).exists():
            if self.logger:
                self.logger.log('info', f'✅ Found {tool_name} via {env_var}: {env_path}')
            return env_path
        
        if self.logger:
            self.logger.log('warning', f'❌ {tool_name} not found')
        return None
    
    def get_all_tools(self):
        """Get paths for all known tools"""
        tools = {}
        for tool_name in self.tool_paths.keys():
            tools[tool_name] = self.find_tool(tool_name)
        return tools
    
    def check_requirements(self, required_tools):
        """Check if all required tools are available"""
        missing_tools = []
        found_tools = {}
        
        for tool in required_tools:
            path = self.find_tool(tool)
            if path:
                found_tools[tool] = path
            else:
                missing_tools.append(tool)
        
        return found_tools, missing_tools
    
    def get_installation_instructions(self, tool_name):
        """Get installation instructions for missing tools"""
        instructions = {
            'mkvextract': {
                'windows': 'Download MKVToolNix from https://www.mkvtoolnix.download/',
                'linux': 'sudo apt install mkvtoolnix (Ubuntu/Debian) or sudo yum install mkvtoolnix (CentOS/RHEL)',
                'darwin': 'brew install mkvtoolnix'
            },
            'ffmpeg': {
                'windows': 'Download from https://ffmpeg.org/ or use chocolatey: choco install ffmpeg',
                'linux': 'sudo apt install ffmpeg (Ubuntu/Debian) or sudo yum install ffmpeg (CentOS/RHEL)',
                'darwin': 'brew install ffmpeg'
            },
            'tesseract': {
                'windows': 'Download from https://github.com/UB-Mannheim/tesseract/wiki',
                'linux': 'sudo apt install tesseract-ocr (Ubuntu/Debian) or sudo yum install tesseract (CentOS/RHEL)',
                'darwin': 'brew install tesseract'
            }
        }
        
        if tool_name in instructions and self.system in instructions[tool_name]:
            return instructions[tool_name][self.system]
        
        return f"Please install {tool_name} for your platform"