from langdetect import detect
from langdetect.lang_detect_exception import LangDetectException
import re
from .file_utils import safe_read_subtitle

class LanguageDetector:
    def __init__(self, logger):
        self.logger = logger
        self._re_number = re.compile(r'^\d+$')

    def detect_language(self, subtitle_file):
        try:
            text, _enc = safe_read_subtitle(subtitle_file)
            
            # Extrair texto limpo
            clean_text = self.extract_text(text)
            if not clean_text:
                return None
            lang = detect(clean_text)
            self.logger.log('info', f'Idioma detectado para {subtitle_file}: {lang}')
            return lang
        except LangDetectException:
            self.logger.log('warning', f'Não foi possível detectar idioma para {subtitle_file}')
            return None
        except Exception as e:
            self.logger.log('error', f'Erro ao detectar idioma: {str(e)}')
            return None

    def extract_text(self, content):
        # Remover timecodes e formatação
        lines = content.split('\n')
        text_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if self._re_number.match(stripped):
                continue
            if '-->' in stripped:
                continue
            if stripped:
                text_lines.append(line)
        return ' '.join(text_lines)

    def is_translatable(self, lang):
        if not lang:
            return True
        lang = str(lang).lower()
        if lang in ('pt', 'pt-br', 'pt_br'):
            return False  # Já em português
        return True