import deepl
import time
import re
import html
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
try:
    from googletrans import Translator as GoogleTranslator
except ImportError:
    GoogleTranslator = None
try:
    import openai
    # Detect SDK version: v1.x uses openai.OpenAI(), v0.x uses openai.ChatCompletion
    _OPENAI_V1 = hasattr(openai, 'OpenAI')
except ImportError:
    openai = None
    _OPENAI_V1 = False
try:
    import google.generativeai as genai
except ImportError:
    genai = None
try:
    from libretranslate import LibreTranslateAPI as LibreTranslateApi
except ImportError:
    LibreTranslateApi = None
try:
    import requests
except ImportError:
    requests = None
try:
    from .ollama_client import OllamaManager
except ImportError:
    OllamaManager = None
try:
    from .ctranslate2_client import CTranslate2Manager
except ImportError:
    CTranslate2Manager = None
try:
    from .argos_client import ArgosTranslateManager
except ImportError:
    ArgosTranslateManager = None
try:
    from .translation_optimizer import TranslationOptimizer, SmartBatcher
except ImportError:
    TranslationOptimizer = None
    SmartBatcher = None
try:
    from .glossary_manager import GlossaryManager
except ImportError:
    GlossaryManager = None
try:
    from .prompt_builder import PromptBuilder, TranslationProfile, TranslationJob, SeriesMetadata
except ImportError:
    PromptBuilder = None
    TranslationProfile = None
    TranslationJob = None
    SeriesMetadata = None
try:
    from .line_classifier import LineClassifier, LineType
except ImportError:
    LineClassifier = None
    LineType = None
try:
    from .fewshot_examples import get_fewshot_examples
except ImportError:
    get_fewshot_examples = None
try:
    from .quality_validator import SubtitleQualityValidator
except ImportError:
    SubtitleQualityValidator = None
from .file_utils import safe_read_subtitle, safe_write_subtitle
from .translation_cache import TranslationCache
import langdetect

class Translator:
    def __init__(self, keys, logger, api_type='Ollama', translation_callback=None, max_parallelism=1):
        self.logger = logger
        self.api_type = api_type
        self.keys = keys
        self.translation_callback = translation_callback
        self.stop_processing = False  # Flag for stopping translation
        self.max_parallelism = max(1, min(2, max_parallelism))  # Clamp between 1-2 for GPU safety
        # Apenas a API selecionada - sem fallback para outras APIs (Gemini etc. abandonado)
        self.api_order = [api_type]
        self.translators = {}
        self.api_status = self._load_api_status()
        
        # Translation statistics for detailed logging
        self.translation_stats = {
            'total_translations': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'validation_rejections': 0,
            'api_failures': 0,
            'identical_translations': 0,  # When translation == original
            'successful_translations': 0
        }
        
        # Batch translation stats (for auto-disable if too many failures)
        self.batch_translation_enabled = False  # Desabilitado por padrão, linha-por-linha com contexto é mais confiável
        self.batch_failure_count = 0
        self.batch_success_count = 0
        # Micro-batch de 2 linhas por request (opcional, default off) – menos requests, mesma qualidade
        self.use_micro_batch_2 = bool(keys.get('use_micro_batch_2', False))
        # Batch SRT: 0 = desligado, 4/8/12 = linhas por request com fallback
        self.srt_batch_size = int(keys.get('srt_batch_size', 0))
        if self.srt_batch_size not in (0, 2, 4, 6, 8, 10, 12):
            self.srt_batch_size = 0
        # Batch ASS: 1 = linha a linha, 2 = par, 4/8/12 = blocos com fallback
        self.ass_batch_size = int(keys.get('ass_batch_size', 2))
        if self.ass_batch_size not in (1, 2, 4, 6, 8, 10, 12):
            self.ass_batch_size = 2
        # Usar prompt de batch (N entradas → N saídas) para batches maiores com mesma qualidade
        self.use_batch_prompt = bool(keys.get('use_batch_prompt', False))

        # Context for line-by-line translation (for better quality)
        self.translation_context = []  # Buffer of recent translations
        
        # Initialize translation cache
        self.cache = TranslationCache()
        
        # Clean up bad translations from cache (non-translations that are identical to original)
        try:
            bad_count = self.cache.cleanup_bad_translations()
            if bad_count > 0:
                self.logger.log('info', f'🧹 Limpou {bad_count} traduções ruins do cache')
        except Exception as e:
            self.logger.log('warning', f'Falha ao limpar cache: {str(e)}')
        
        # Initialize translation optimizer for deduplication and batching
        self.optimizer = TranslationOptimizer() if TranslationOptimizer else None
        self.batcher = SmartBatcher() if SmartBatcher else None
        
        # Initialize GlossaryManager for anime subtitle glossaries
        self.glossary_manager = GlossaryManager(logger=self.logger) if GlossaryManager else None
        self.current_series_glossary = None

        # ──── Novos componentes v2 ────
        # TranslationProfile centralizado
        self.profile = TranslationProfile.from_config(keys) if TranslationProfile else None

        # PromptBuilder
        self.prompt_builder = PromptBuilder(
            profile=self.profile,
            glossary_manager=self.glossary_manager,
        ) if PromptBuilder and self.profile else None

        # LineClassifier
        self.line_classifier = LineClassifier() if LineClassifier else None

        # Quality validator (instância persistente)
        self._quality_validator = SubtitleQualityValidator(logger=self.logger) if SubtitleQualityValidator else None

        # TranslationJob atual (isolamento por arquivo)
        self.current_job: 'TranslationJob' = None

        # Cache de modelos do Ollama (evita chamadas repetidas em /api/tags)
        self._ollama_models_cache = None
        self._ollama_models_cache_at = 0.0
        self._ollama_models_cache_ttl_s = 30.0

        # Evita downloads duplicados do mesmo modelo em múltiplas threads
        self._ollama_pull_lock = threading.Lock()
        self._ollama_pulls_inflight = set()
        self._ollama_warmed = False  # Warmup uma vez por sessão para reduzir latência da primeira tradução

        self.system_prompt = """Tradutor de legendas EN→PT-BR. Regras:
- Responda SOMENTE com a tradução, sem explicações
- Português brasileiro natural e coloquial
- Preserve formatação, tags e pontuação
- Não traduza nomes próprios ou efeitos sonoros
TRADUZA:"""

        # Prompt dedicado para modo batch (N linhas in → N linhas out, formato explícito)
        self.system_prompt_batch = """Você é um tradutor de legendas. Sua ÚNICA tarefa é receber N linhas numeradas (inglês) e devolver EXATAMENTE N linhas no MESMO formato e ordem.

FORMATO DE SAÍDA OBRIGATÓRIO (uma linha por número, sem pular nenhum):
1│ tradução da linha 1 em português do Brasil
2│ tradução da linha 2
3│ tradução da linha 3
... (até N│)

REGRAS:
- Devolva EXATAMENTE o mesmo número de linhas que recebeu, na mesma ordem (1, 2, 3, …).
- Use SOMENTE o formato "número│ texto" por linha. Sem cabeçalho, sem rodapé, sem explicações.
- Português brasileiro natural; preserve tom, gírias e continuidade do diálogo.
- Mantenha tags ASS/SRT ({\\i1}, {\\an8}, etc.) e formatação; não traduza nomes próprios, (*efeitos*), [notas].
- Se uma linha for só efeito sonoro ou nome, repita igual com o mesmo número."""

        # Full rules only when needed
        self.full_translation_rules = self._get_translation_rules()
        for api in self.api_order:
            try:
                if api == 'Argos':
                    if ArgosTranslateManager:
                        argos = ArgosTranslateManager(self.logger)
                        if argos.is_available():
                            self.translators[api] = argos
                elif api == 'CTranslate2':
                    if CTranslate2Manager:
                        ct2 = CTranslate2Manager(self.logger)
                        if ct2.is_available():
                            self.translators[api] = ct2
                elif api == 'DeepL':
                    if self._is_api_available('DeepL'):
                        self.translators[api] = deepl.Translator(keys.get('deepl', ''))
                elif api == 'Ollama':
                    if requests:
                        # Built-in Ollama client with optimized model
                        self.translators[api] = {
                            'url': keys.get('ollama_url', 'http://localhost:11434'),
                            'model': keys.get('ollama_model', 'qwen2.5:32b-instruct-q4_K_M')
                        }
                        # Validate Ollama connection on init
                        self._validate_ollama_connection()
                elif api == 'Google':
                    if GoogleTranslator:
                        self.translators[api] = GoogleTranslator()
                elif api == 'GPT':
                    if openai and self._is_api_available('GPT'):
                        if _OPENAI_V1:
                            self.translators[api] = openai.OpenAI(api_key=keys.get('gpt', ''))
                        else:
                            openai.api_key = keys.get('gpt', '')
                            self.translators[api] = True
                elif api == 'LibreTranslate':
                    if LibreTranslateApi:
                        url = keys.get('libre_url', 'https://libretranslate.com')
                        self.translators[api] = LibreTranslateApi(url)
                elif api == 'Gemini':
                    if genai and self._is_api_available('Gemini'):
                        genai.configure(api_key=keys.get('gemini', ''))
                        self.translators[api] = genai
            except Exception as e:
                self.logger.log('debug', f'Falha ao inicializar API {api}: {e}')

    def _get_translation_rules(self):
        """Get strict translation rules for all APIs"""
        return r"""REGRAS ESTRITAS – SIGA TODAS SEM EXCEÇÃO:

1. Preserve 100% da formatação original:
   - Numeração das legendas (ex: 1, 2, 3...)
   - Timestamps EXATOS (ex: 00:01:23,456 --> 00:01:25,789)
   - Tags ASS/SSA (ex: {\an8}, {\i1}, {\bord0}, etc.)
   - Quebras de linha dentro da mesma legenda
   - Itálico, negrito, cores, posições – NUNCA altere ou remova

2. TRADUZA SOMENTE o texto falado / diálogo.
   - NUNCA traduza:
     - Nomes próprios de personagens, lugares, marcas, objetos mágicos
     - Efeitos sonoros entre parênteses ou colchetes (ex: (gunshot), [risadas], *suspiro*)
     - Letras de música entre ♪ ♪
     - Onomatopeias que não sejam faladas (ex: Pow! Bang!)
     - Termos técnicos ou jargões que fazem parte do universo da obra

3. Use português brasileiro natural e coloquial:
   - Contrações comuns: tá, né, cê, pra, tô, etc. (quando o tom permite)
   - Gírias e palavrões preservados no nível de intensidade do original
   - Mantenha tom: sarcástico, agressivo, fofo, formal, etc.
   - Evite português de Portugal (nada de "tu", "à", "ó", "tipo assim")

4. Fidelidade máxima:
   - Não invente, não omita, não adicione explicações
   - Mantenha o mesmo número exato de linhas / blocos
   - Se uma frase for muito longa para caber em legenda, mantenha natural mas não quebre artificialmente

5. Resposta:
   - RETORNE APENAS o bloco de legendas traduzido, na mesma ordem e formato
   - NÃO adicione introdução, explicação, nota, "Aqui está a tradução:", nem nada fora do formato de legenda
   - NÃO use ```srt ou markdown – saia texto puro como o original

Traduza para português brasileiro:"""

    def _load_api_status(self):
        """Load saved API status from file"""
        try:
            import json
            with open('api_status.json', 'r') as f:
                return json.load(f)
        except Exception:
            return {'DeepL': 'unknown', 'GPT': 'unknown', 'Gemini': 'unknown'}
    
    def _save_api_status(self):
        """Save API status to file"""
        try:
            import json
            with open('api_status.json', 'w') as f:
                json.dump(self.api_status, f)
        except Exception as e:
            self.logger.log('error', f'Erro ao salvar status das APIs: {e}')
    
    def check_single_api(self, api_name):
        """Check status of a single API"""
        if api_name == 'DeepL' and self.keys.get('deepl'):
            try:
                temp_translator = deepl.Translator(self.keys.get('deepl'))
                temp_translator.translate_text('test', target_lang='pt')
                self.api_status['DeepL'] = 'available'
                self.logger.log('info', '✅ DeepL API: Disponível')
            except Exception as e:
                if 'quota' in str(e).lower() or 'limit' in str(e).lower():
                    self.api_status['DeepL'] = 'quota_exceeded'
                    self.logger.log('warning', '⚠️ DeepL API: Quota excedida')
                else:
                    self.api_status['DeepL'] = 'error'
                    self.logger.log('error', f'❌ DeepL API: Erro - {e}')
        
        elif api_name == 'GPT' and self.keys.get('gpt') and openai:
            try:
                if _OPENAI_V1:
                    client = openai.OpenAI(api_key=self.keys.get('gpt'))
                    client.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": "test"}],
                        max_tokens=1,
                    )
                else:
                    openai.api_key = self.keys.get('gpt')
                    openai.ChatCompletion.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": "test"}],
                        max_tokens=1,
                    )
                self.api_status['GPT'] = 'available'
                self.logger.log('info', '✅ GPT API: Disponível')
            except Exception as e:
                if 'quota' in str(e).lower() or 'limit' in str(e).lower() or 'billing' in str(e).lower():
                    self.api_status['GPT'] = 'quota_exceeded'
                    self.logger.log('warning', '⚠️ GPT API: Quota excedida')
                else:
                    self.api_status['GPT'] = 'error'
                    self.logger.log('error', f'❌ GPT API: Erro - {e}')
        
        elif api_name == 'Gemini' and self.keys.get('gemini') and genai:
            try:
                genai.configure(api_key=self.keys.get('gemini'))
                model = genai.GenerativeModel('gemini-pro')
                model.generate_content('test')
                self.api_status['Gemini'] = 'available'
                self.logger.log('info', '✅ Gemini API: Disponível')
            except Exception as e:
                if 'quota' in str(e).lower() or 'limit' in str(e).lower():
                    self.api_status['Gemini'] = 'quota_exceeded'
                    self.logger.log('warning', '⚠️ Gemini API: Quota excedida')
                else:
                    self.api_status['Gemini'] = 'error'
                    self.logger.log('error', f'❌ Gemini API: Erro - {e}')
        
        self._save_api_status()
        return self.api_status.get(api_name, 'unknown')
    
    def reset_api_quota(self, api_name):
        """Reset API quota status (mark as available)"""
        self.api_status[api_name] = 'available'
        self._save_api_status()
        self.logger.log('info', f'🔄 {api_name} API: Status resetado para disponível')
    
    def _validate_ollama_connection(self):
        """Validate Ollama connection on initialization and auto-download model if needed"""
        if 'Ollama' not in self.translators or not requests:
            return False
        
        try:
            config = self.translators['Ollama']
            url = config['url'].rstrip('/')
            model = config['model']

            # Check if model is available (usando cache)
            models = self._get_ollama_models(url)
            if not models:
                self.logger.log('warning', f'⚠️ Ollama não está respondendo em {url}')
                return False

            model_names = [m.get('name', '') for m in models]
            
            if not any(model in name for name in model_names):
                self.logger.log('warning', f'⚠️ Modelo {model} não encontrado em Ollama')
                self.logger.log('info', f'📥 Iniciando download automático do modelo {model}...')
                
                # Try to auto-download the model
                if self._auto_download_model(url, model):
                    self.logger.log('info', f'✅ Modelo {model} baixado com sucesso!')
                    self._invalidate_ollama_models_cache()
                    return True
                # Fallback: qwen2.5:32b-instruct-q4_K_M -> qwen3:8b (Ollama oficial)
                if model == 'qwen2.5:32b-instruct-q4_K_M':
                    self.logger.log('info', f'📥 Tentando modelo alternativo qwen3:8b...')
                    if self._auto_download_model(url, 'qwen3:8b'):
                        config['model'] = 'qwen3:8b'
                        self.logger.log('info', f'✅ Modelo qwen3:8b baixado com sucesso!')
                        self._invalidate_ollama_models_cache()
                        return True
                self.logger.log('error', f'❌ Falha ao baixar modelo {model}')
                self.logger.log('info', f'💡 Você pode baixar manualmente com: ollama pull {model}')
                return False
            
            self.logger.log('info', f'✅ Ollama conectado com modelo {model}')
            return True
            
        except requests.exceptions.ConnectionError:
            self.logger.log('error', f'❌ Não foi possível conectar ao Ollama em {url}. Certifique-se de que o Ollama está rodando.')
            return False
        except Exception as e:
            self.logger.log('error', f'❌ Erro ao validar Ollama: {e}')
            return False

    def _invalidate_ollama_models_cache(self):
        self._ollama_models_cache = None
        self._ollama_models_cache_at = 0.0

    def _get_ollama_models(self, ollama_url: str):
        """Return models from /api/tags with a short TTL cache."""
        now = time.time()
        if self._ollama_models_cache is not None and (now - self._ollama_models_cache_at) < self._ollama_models_cache_ttl_s:
            return self._ollama_models_cache

        try:
            response = requests.get(f'{ollama_url}/api/tags', timeout=5)
            if response.status_code != 200:
                return []
            models = response.json().get('models', [])
            self._ollama_models_cache = models
            self._ollama_models_cache_at = now
            return models
        except Exception:
            return []
    
    def _auto_download_model(self, ollama_url: str, model_name: str) -> bool:
        """Automatically download a model from Ollama"""
        try:
            url = f"{ollama_url}/api/pull"
            payload = {"name": model_name}
            
            # Use streaming to get progress updates
            # connect timeout curto + read timeout longo (download grande)
            response = requests.post(url, json=payload, timeout=(10, 3600), stream=True)
            
            if response.status_code == 200:
                import json

                # Throttle logs + avoid repeating identical statuses
                last_log_at = 0.0
                last_logged = {
                    'status': None,
                    'digest': None,
                    'pct': None,
                }

                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue

                    status = str(data.get('status', '')).strip()
                    digest = data.get('digest')
                    total = data.get('total')
                    completed = data.get('completed')

                    pct = None
                    if isinstance(total, (int, float)) and total > 0 and isinstance(completed, (int, float)):
                        pct = int((completed / total) * 100)

                    now = time.time()
                    should_log = False

                    # Log when status changes, digest changes, or pct crosses 5% step
                    if status and status != last_logged['status']:
                        should_log = True
                    if digest and digest != last_logged['digest']:
                        should_log = True
                    if pct is not None:
                        prev_pct = last_logged['pct']
                        if prev_pct is None or pct >= prev_pct + 5 or pct == 100:
                            should_log = True

                    # Hard throttle to at most ~1 msg/sec
                    if should_log and (now - last_log_at) >= 1.0:
                        if pct is not None:
                            msg = f'📥 {status} ({pct}%)'
                        else:
                            msg = f'📥 {status}'
                        self.logger.log('info', msg)
                        last_log_at = now
                        last_logged['status'] = status or last_logged['status']
                        last_logged['digest'] = digest or last_logged['digest']
                        last_logged['pct'] = pct if pct is not None else last_logged['pct']

                self.logger.log('info', f'✅ Download concluído: {model_name}')
                return True
            else:
                self.logger.log('error', f'❌ Erro ao fazer download: HTTP {response.status_code}')
                return False
                
        except requests.exceptions.Timeout:
            self.logger.log('error', f'❌ Timeout ao baixar modelo {model_name} (leva tempo, tente novamente depois)')
            return False
        except Exception as e:
            self.logger.log('error', f'❌ Erro ao fazer download automático: {str(e)}')
            return False
    
    def _ensure_ollama_model_available(self) -> bool:
        """Ensure Ollama model is available, downloading if necessary"""
        if 'Ollama' not in self.translators or not requests:
            return False
        
        try:
            config = self.translators['Ollama']
            url = config['url'].rstrip('/')
            model = config['model']

            models = self._get_ollama_models(url)
            if not models:
                self.logger.log('error', f'❌ Ollama não está respondendo')
                return False

            model_names = [m.get('name', '') for m in models]
            
            # Model already available
            if any(model in name for name in model_names):
                if not getattr(self, '_ollama_warmed', False):
                    self._ollama_warmup(url, model)
                    self._ollama_warmed = True
                return True
            
            # Model not available, try to download (com trava para evitar downloads duplicados)
            with self._ollama_pull_lock:
                # Se outra thread já está baixando esse modelo, aguarda de forma simples
                if model in self._ollama_pulls_inflight:
                    self.logger.log('info', f'📥 Download do modelo {model} já está em andamento...')
                else:
                    self._ollama_pulls_inflight.add(model)

                try:
                    # Re-check após pegar lock (cache pode estar desatualizado)
                    self._invalidate_ollama_models_cache()
                    models = self._get_ollama_models(url)
                    model_names = [m.get('name', '') for m in models]
                    if any(model in name for name in model_names):
                        return True

                    self.logger.log('warning', f'⚠️ Modelo {model} não encontrado, iniciando download automático...')
                    ok = self._auto_download_model(url, model)
                    # Fallback: se pediu qwen2.5:32b-instruct-q4_K_M e não existe, tentar qwen3:8b (Ollama oficial)
                    if not ok and model == 'qwen2.5:32b-instruct-q4_K_M':
                        self.logger.log('info', f'📥 Tentando modelo alternativo qwen3:8b...')
                        ok = self._auto_download_model(url, 'qwen3:8b')
                        if ok:
                            config['model'] = 'qwen3:8b'
                            model = 'qwen3:8b'
                    if ok:
                        self._invalidate_ollama_models_cache()
                        if not getattr(self, '_ollama_warmed', False):
                            self._ollama_warmup(url, model)
                            self._ollama_warmed = True
                    return ok
                finally:
                    self._ollama_pulls_inflight.discard(model)
            
        except Exception as e:
            self.logger.log('error', f'❌ Erro ao verificar modelo: {str(e)}')
            return False

    def _ollama_warmup(self, url, model):
        """Uma requisição mínima para carregar o modelo na memória e reduzir latência da primeira tradução."""
        if not requests:
            return
        try:
            r = requests.post(
                f'{url}/api/generate',
                json={'model': model, 'prompt': 'Translate to Portuguese: Hi', 'stream': False},
                timeout=30
            )
            if r.status_code == 200:
                self.logger.log('debug', 'Ollama warmup OK')
            else:
                self.logger.log('warning', f'Ollama warmup: {r.status_code}')
        except requests.exceptions.Timeout:
            self.logger.log('warning', 'Ollama warmup timeout (primeira tradução pode demorar mais)')
        except Exception as e:
            self.logger.log('debug', f'Ollama warmup: {e}')
    
    def _is_api_available(self, api_name):
        """Check if API is available (not quota exceeded)"""
        return self.api_status.get(api_name, 'unknown') != 'quota_exceeded'
    def translate_subtitle(self, subtitle_file, target_lang='pt-BR', progress_callback=None,
                           series_metadata=None):
        """
        Traduz um arquivo de legenda.
        
        Args:
            subtitle_file: Path do arquivo
            target_lang: Idioma alvo
            progress_callback: Callback de progresso
            series_metadata: SeriesMetadata opcional (passado pelo gui_sonarr)
        """
        try:
            # ── Criar TranslationJob isolado para este arquivo ──
            job = TranslationJob(
                profile=self.profile or TranslationProfile(),
            ) if TranslationJob else None

            # Propagar metadados da série se disponíveis
            if job and series_metadata and SeriesMetadata:
                if isinstance(series_metadata, dict):
                    job.series_metadata = SeriesMetadata(
                        title=series_metadata.get('title', ''),
                        genres=series_metadata.get('genres', []),
                        characters=series_metadata.get('characters', []),
                        series_type=series_metadata.get('series_type', ''),
                    )
                elif isinstance(series_metadata, SeriesMetadata):
                    job.series_metadata = series_metadata

            # Extrair series_id e carregar glossário (disco ou Sonarr/AniList)
            series_id = self._extract_series_id_from_path(str(subtitle_file)) if hasattr(subtitle_file, '__fspath__') or isinstance(subtitle_file, (str,)) else None
            if not series_id and hasattr(subtitle_file, 'resolve'):
                series_id = self._extract_series_id_from_path(str(subtitle_file.resolve()))
            self.current_series_glossary = None
            if self.glossary_manager and series_id:
                try:
                    if self.keys.get('sonarr_url') and self.keys.get('sonarr_api_key'):
                        self.current_series_glossary = self.glossary_manager.fetch_from_sonarr_and_anilist(
                            sonarr_url=self.keys['sonarr_url'],
                            api_key=self.keys['sonarr_api_key'],
                            series_id=series_id
                        )
                    else:
                        disk = self.glossary_manager.load_from_disk(series_id)
                        if disk:
                            self.glossary_manager._series_v2[series_id] = disk
                            self.current_series_glossary = self.glossary_manager.get_budgeted_glossary(series_id)
                    if self.current_series_glossary:
                        self.logger.log('info', f'✅ Carregado glossário para série {series_id}: {len(self.current_series_glossary)} termos')
                except Exception as e:
                    self.logger.log('warn', f'Erro ao carregar glossário da série: {e}')

            # Atribuir glossário ao job
            if job:
                job.series_glossary = self.current_series_glossary
            self.current_job = job

            # Determine output extension
            original_ext = subtitle_file.suffix.lower() if hasattr(subtitle_file, 'suffix') else '.srt'
            output_ext = '.ass' if original_ext == '.sub' else original_ext
            output_file = subtitle_file.with_name(subtitle_file.stem + f'.{target_lang}' + output_ext)
            if output_file.exists():
                self.logger.log('info', f'✅ Arquivo já traduzido: {output_file.name}')
                return output_file

            # Ler conteúdo (necessário para pre-scan e tradução)
            content, detected_encoding = safe_read_subtitle(subtitle_file)
            self.logger.log('info', f'📖 Arquivo lido com encoding: {detected_encoding}')

            # Pre-scan com modelo leve (uma vez por série, quando episodes_scanned == 0)
            if self.glossary_manager and series_id and self.profile and getattr(self.profile, 'enable_auto_glossary', True):
                v2 = self.glossary_manager._series_v2.get(series_id) or self.glossary_manager.load_from_disk(series_id)
                if v2 is not None:
                    self.glossary_manager._series_v2[series_id] = v2
                if (v2 is None or v2.get('episodes_scanned', 0) == 0) and content:
                    dialogue_lines = self._extract_dialogue_lines_for_prescan(content)
                    if dialogue_lines and not self.stop_processing:
                        series_title = (job.series_metadata.title if job and job.series_metadata else None) or 'Unknown'
                        glossary_model = self.keys.get('glossary_model') or self.keys.get('ollama_model', 'qwen2.5:7b-instruct')
                        prescan = self.glossary_manager.generate_glossary_with_llm(
                            series_title=series_title,
                            subtitle_lines=dialogue_lines,
                            ollama_url=self.keys.get('ollama_url', 'http://localhost:11434'),
                            model=glossary_model,
                            stop_check=lambda: self.stop_processing,
                        )
                        if prescan:
                            self.glossary_manager.merge_prescan_terms(series_id, prescan)
                        self.current_series_glossary = self.glossary_manager.get_budgeted_glossary(series_id)
                        if job:
                            job.series_glossary = self.current_series_glossary

            # Detect format based on content (not extension)
            if content.strip().startswith('[Script Info]') or content.strip().startswith('Dialogue:'):
                translated_content = self.translate_ass(content, target_lang, progress_callback)
            else:
                translated_content = self.translate_srt(content, target_lang, progress_callback)

            # Quality validation (log warning but always save — never discard a full translation)
            validator = self._quality_validator or SubtitleQualityValidator(self.logger)
            
            is_valid, validation_message = validator.validate_translation_quality(content, translated_content)
            if not is_valid:
                self.logger.log('warning', f'⚠️ Qualidade abaixo do ideal: {validation_message} (salvando mesmo assim)')

            # Save with UTF-8 encoding (standard for modern subtitles)
            safe_write_subtitle(output_file, translated_content, 'utf-8')
            
            self.logger.log('info', f'Tradução salva em {output_file}')
            
            # Log cache statistics
            cache_stats = self.cache.get_stats()
            self.logger.log('info', f'Cache: {cache_stats["total_entries"]} entradas, {cache_stats["total_hits"]} hits')
            
            # Log translation statistics summary
            self._log_translation_stats()

            # Log job-level stats if available
            if job:
                self._log_job_stats(job)

            # Auto-merge termos sugeridos no glossário da série e persistir em disco
            if series_id and self.glossary_manager and job:
                suggested = job.get_suggested_glossary(min_occurrences=3)
                if suggested:
                    self.glossary_manager.merge_suggested_terms(series_id, suggested)
            
            # Limpar job ao finalizar
            self.current_job = None
            
            return output_file
        except Exception as e:
            self.logger.log('error', f'Erro na tradução: {str(e)}')
            self.current_job = None
            return None

    def _log_job_stats(self, job):
        """Log estatísticas do TranslationJob."""
        if not job or not hasattr(job, 'stats'):
            return
        s = job.stats
        total = s.get('total_lines', 0)
        if total == 0:
            return
        self.logger.log('info', f'📊 Job stats: {s["classified_dialogue"]} diálogos, '
                        f'{s["classified_sfx"]} SFX, {s["classified_music"]} música, '
                        f'{s["classified_tag"]} tags, {s["classified_untranslatable"]} intocáveis, '
                        f'{s["self_consistency_triggered"]} self-consistency')

        # Log glossário sugerido
        suggested = job.get_suggested_glossary(min_occurrences=3)
        if suggested:
            self.logger.log('info', f'📝 Glossário sugerido ({len(suggested)} termos): '
                           f'{dict(list(suggested.items())[:5])}')

    def translate_srt(self, content, target_lang, progress_callback=None):
        # Verificar se modelo Ollama está disponível antes de traduzir
        if self.api_type == 'Ollama' and 'Ollama' in self.translators:
            if not self._ensure_ollama_model_available():
                self.logger.log('error', 'Modelo Ollama não disponível, tradução abortada')
                return content
        
        # Traduzir SRT mantendo o formato
        lines = content.split('\n')
        texts_to_translate = []
        text_indices = []
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            # Skip empty lines and sequence numbers
            if not line or line.isdigit():
                i += 1
                continue
                
            # Skip timestamp lines
            if '-->' in line:
                i += 1
                continue
                
            # This should be subtitle text
            clean_text = self.clean_subtitle_text(line)
            if clean_text.strip() and self.is_translatable_text(clean_text):
                texts_to_translate.append(clean_text)
                text_indices.append(i)
            i += 1
            
        if texts_to_translate:
            self.logger.log('info', f'Encontrados {len(texts_to_translate)} textos para traduzir')
            
            # Deduplicação: traduzir cada texto único uma vez e repor nas posições originais
            if self.optimizer:
                unique_texts, duplicate_map = self.optimizer.deduplicate_texts(texts_to_translate)
                self.logger.log('info', f'Após deduplicação: {len(unique_texts)} textos únicos de {len(texts_to_translate)} totais')
            else:
                # Dedupe local quando TranslationOptimizer não existe (mesmo benefício que ASS)
                seen = {}
                unique_texts = []
                duplicate_map = []  # duplicate_map[i] = índice em unique_texts para texts_to_translate[i]
                for t in texts_to_translate:
                    if t not in seen:
                        seen[t] = len(unique_texts)
                        unique_texts.append(t)
                    duplicate_map.append(seen[t])
                if len(unique_texts) < len(texts_to_translate):
                    self.logger.log('info', f'Deduplicação SRT: {len(unique_texts)} únicos de {len(texts_to_translate)} linhas')

            # Batch SRT (4/6/8 linhas por request) quando ativado e API Ollama
            srt_batch = getattr(self, 'srt_batch_size', 0)
            translated_texts = []
            if srt_batch >= 4 and self.api_type == 'Ollama' and len(unique_texts) >= srt_batch:
                self.logger.log('info', f'SRT em batches de {srt_batch} linhas (pode desativar nas opções)')
                num_chunks = (len(unique_texts) + srt_batch - 1) // srt_batch
                for chunk_start in range(0, len(unique_texts), srt_batch):
                    if self.stop_processing:
                        break
                    chunk = unique_texts[chunk_start:chunk_start + srt_batch]
                    batch_result = self._translate_batch_with_context_ollama(chunk, [], target_lang)
                    if batch_result and len(batch_result) == len(chunk):
                        translated_texts.extend(batch_result)
                    else:
                        self.logger.log('debug', f'Batch SRT falhou para chunk, fallback linha a linha')
                        fallback = self.translate_batch_optimized(chunk, target_lang, None)
                        translated_texts.extend(fallback)
                    if progress_callback and num_chunks > 0:
                        done = (chunk_start + len(chunk)) // srt_batch
                        progress_callback(done / num_chunks * 100)
            else:
                # Comportamento original: smart batching ou translate_batch_optimized
                if self.batcher and len(unique_texts) > 5:
                    batches = self.batcher.create_batches(unique_texts)
                    for batch in batches:
                        if self.stop_processing:
                            break
                        batch_results = self.translate_batch_optimized(batch, target_lang, progress_callback)
                        translated_texts.extend(batch_results)
                        if self.optimizer:
                            self.optimizer.apply_rate_limiting()
                else:
                    translated_texts = self.translate_batch_optimized(unique_texts, target_lang, progress_callback)
            
            # Restaurar duplicatas nas posições originais
            if self.optimizer and duplicate_map:
                translated_texts = self.optimizer.reorder_results(translated_texts, duplicate_map)
            elif not self.optimizer and duplicate_map:
                translated_texts = [translated_texts[j] for j in duplicate_map]
            
            for idx, trans in zip(text_indices, translated_texts):
                # Clean translated text and replace
                clean_trans = self._clean_ai_response(trans)
                lines[idx] = clean_trans
        else:
            self.logger.log('warning', f'Nenhum texto encontrado no SRT para traduzir')
            
        return '\n'.join(lines)
    
    def clean_subtitle_text(self, text):
        """Clean subtitle text from formatting codes"""
        import re
        # Remove HTML tags
        text = re.sub(r'<[^>]*>', '', text)
        # Remove ASS override tags
        text = re.sub(r'\{[^}]*\}', '', text)
        # Decode HTML entities
        text = html.unescape(text)
        return text.strip()

    def _extract_dialogue_lines_for_prescan(self, content: str, max_lines: int = 80) -> list:
        """Extrai até max_lines de diálogo do conteúdo (SRT ou ASS) para pre-scan do glossário."""
        lines_out = []
        content = content.strip()
        if content.startswith('[Script Info]') or content.startswith('Dialogue:'):
            for line in content.split('\n'):
                if line.startswith('Dialogue:'):
                    parts = line.split(',', 9)
                    if len(parts) >= 10:
                        text = self.extract_text_from_ass(parts[9]) if hasattr(self, 'extract_text_from_ass') else parts[9]
                        if text.strip():
                            lines_out.append(text.strip())
                            if len(lines_out) >= max_lines:
                                break
        else:
            blocks = content.split('\n\n')
            for block in blocks:
                part = [p.strip() for p in block.split('\n') if p.strip()]
                if len(part) >= 3 and part[0].isdigit() and '-->' in part[1]:
                    for i in range(2, len(part)):
                        line = self.clean_subtitle_text(part[i])
                        if line:
                            lines_out.append(line)
                            if len(lines_out) >= max_lines:
                                return lines_out
        return lines_out[:max_lines]

    def translate_ass(self, content, target_lang, progress_callback=None):
        # Verificar se modelo Ollama está disponível antes de traduzir
        if self.api_type == 'Ollama' and 'Ollama' in self.translators:
            if not self._ensure_ollama_model_available():
                self.logger.log('error', 'Modelo Ollama não disponível, tradução abortada')
                return content
        
        lines = content.split('\n')
        
        # Extract all dialogue lines with their indices
        subtitle_lines = []  # List of (line_idx, clean_text, original_formatted)
        
        for i, line in enumerate(lines):
            if line.startswith('Dialogue:'):
                parts = line.split(',', 9)
                if len(parts) > 9:
                    text = parts[9]
                    # Extract actual text from ASS formatting
                    clean_text = self.extract_text_from_ass(text)
                    if clean_text.strip() and self.is_translatable_text(clean_text):
                        subtitle_lines.append((i, clean_text, text))
        
        if not subtitle_lines:
            return '\n'.join(lines)
        
        self.logger.log('info', f'Traduzindo {len(subtitle_lines)} linhas')
        
        # Use batch translation with context for Ollama (if enabled and enough lines)
        if self.api_type == 'Ollama' and len(subtitle_lines) >= 4 and self.batch_translation_enabled:
            try:
                self.logger.log('info', f'Usando tradução em batch ({len(subtitle_lines)} linhas)')
                
                # Create indexed list for batch processing
                indexed_lines = [(idx, subtitle_lines[idx][0], subtitle_lines[idx][1], subtitle_lines[idx][2]) 
                                for idx in range(len(subtitle_lines))]
                
                # Translate in batches with context
                translated_texts = self.translate_batch_with_context(
                    [(idx, clean_text, formatted_text) for idx, _, clean_text, formatted_text in indexed_lines],
                    batch_size=6,  # 6 lines per batch
                    context_lines=2,  # 2 lines of context
                    target_lang=target_lang
                )
                
                # Check if batch translation succeeded
                if translated_texts and len(translated_texts) == len(subtitle_lines):
                    # Apply translations back to original lines
                    for idx, (line_idx, clean_text, original_formatted) in enumerate(subtitle_lines):
                        if idx < len(translated_texts):
                            translated = translated_texts[idx]
                            parts = lines[line_idx].split(',', 9)
                            parts[9] = self.replace_text_in_ass(original_formatted, translated)
                            lines[line_idx] = ','.join(parts)
                    
                    self.batch_success_count += 1
                    self.logger.log('info', f'✓ Batch translation bem-sucedida')
                    return '\n'.join(lines)
                else:
                    self.logger.log('warning', 'Batch translation retornou resultados incompletos, usando método antigo')
                    self.batch_failure_count += 1
                    
                    # Disable batch if too many failures
                    if self.batch_failure_count >= 3 and self.batch_success_count == 0:
                        self.logger.log('error', '⚠️  Desabilitando tradução em batch devido a muitas falhas')
                        self.batch_translation_enabled = False
                
            except Exception as e:
                self.logger.log('error', f'Batch translation failed: {e}, falling back to old method')
                self.batch_failure_count += 1
                # Fall through to old method
        
        # Fallback: line-by-line com contexto; deduplicação ASS; opcional micro-batch de 2 linhas
        self.logger.log('info', f'Usando tradução linha-por-linha com contexto ({len(subtitle_lines)} linhas)')
        self.translation_context = []  # Reset context buffer
        translated_in_file = {}  # clean_text -> tradução (dedupe dentro do arquivo)
        unique_count = 0
        idx = 0
        while idx < len(subtitle_lines):
            if self.stop_processing:
                break
            line_idx, clean_text, original_formatted = subtitle_lines[idx][0], subtitle_lines[idx][1], subtitle_lines[idx][2]

            if clean_text in translated_in_file:
                clean_translated = translated_in_file[clean_text]
                parts = lines[line_idx].split(',', 9)
                parts[9] = self.replace_text_in_ass(original_formatted, clean_translated)
                lines[line_idx] = ','.join(parts)
                if progress_callback:
                    progress_callback((idx + 1) / len(subtitle_lines) * 100)
                if self.translation_callback and self.is_translatable_text(clean_text):
                    self.translation_callback(clean_text, clean_translated, f"{idx + 1:03d}")
                idx += 1
                continue

            # Batch ASS 4/6/8 linhas (opcional): tentar bloco maior com fallback
            used_batch = False
            ass_batch = getattr(self, 'ass_batch_size', 2)
            if ass_batch >= 4 and self.api_type == 'Ollama':
                # Coletar próximas ass_batch linhas que não estão no cache
                block = []
                for j in range(idx, min(idx + ass_batch, len(subtitle_lines))):
                    _, c, o = subtitle_lines[j][0], subtitle_lines[j][1], subtitle_lines[j][2]
                    if c not in translated_in_file:
                        block.append((subtitle_lines[j][0], c, o))
                    if len(block) >= ass_batch:
                        break
                if len(block) == ass_batch:
                    batch_texts = [b[1] for b in block]
                    ctx = list(self.translation_context)
                    batch_result = self._translate_batch_with_context_ollama(batch_texts, ctx, target_lang)
                    if batch_result and len(batch_result) == ass_batch:
                        for i, (li, _, orig_fmt) in enumerate(block):
                            t = self._clean_ai_response(batch_result[i])
                            if self.glossary_manager and hasattr(self, 'current_series_glossary'):
                                t = self.glossary_manager.apply_to_text(t, series_glossary=self.current_series_glossary)
                            translated_in_file[block[i][1]] = t
                            self.translation_context.append(t)
                            parts = lines[li].split(',', 9)
                            parts[9] = self.replace_text_in_ass(orig_fmt, t)
                            lines[li] = ','.join(parts)
                        ctx_max = self.profile.context_window_size if self.profile else 3
                        self.translation_context = self.translation_context[-ctx_max:]
                        unique_count += ass_batch
                        if progress_callback:
                            progress_callback((idx + ass_batch) / len(subtitle_lines) * 100)
                        if self.translation_callback:
                            for i, (_, c, _) in enumerate(block):
                                if self.is_translatable_text(c):
                                    self.translation_callback(c, batch_result[i], f"{idx + i + 1:03d}")
                        idx += ass_batch
                        used_batch = True
                    else:
                        self.logger.log('debug', f'Batch ASS {ass_batch} falhou, fallback linha a linha')
            if used_batch:
                continue

            # Micro-batch de 2 linhas (quando ass_batch_size>=2 ou batch maior falhou; desligue com ass_batch_size=1)
            used_pair = False
            if not used_batch and ass_batch >= 2 and self.api_type == 'Ollama' and idx + 1 < len(subtitle_lines):
                line_idx2, clean_text2, original_formatted2 = subtitle_lines[idx + 1][0], subtitle_lines[idx + 1][1], subtitle_lines[idx + 1][2]
                if clean_text2 not in translated_in_file:
                    batch_result = self._translate_batch_with_context_ollama(
                        [clean_text, clean_text2],
                        list(self.translation_context),
                        target_lang
                    )
                    if batch_result and len(batch_result) == 2 and batch_result[0] and batch_result[1]:
                        t1 = self._clean_ai_response(batch_result[0])
                        t2 = self._clean_ai_response(batch_result[1])
                        if self.glossary_manager and hasattr(self, 'current_series_glossary'):
                            t1 = self.glossary_manager.apply_to_text(t1, series_glossary=self.current_series_glossary)
                            t2 = self.glossary_manager.apply_to_text(t2, series_glossary=self.current_series_glossary)
                        translated_in_file[clean_text] = t1
                        translated_in_file[clean_text2] = t2
                        self.translation_context.extend([t1, t2])
                        ctx_max = self.profile.context_window_size if self.profile else 3
                        self.translation_context = self.translation_context[-ctx_max:]
                        unique_count += 2
                        parts = lines[line_idx].split(',', 9)
                        parts[9] = self.replace_text_in_ass(original_formatted, t1)
                        lines[line_idx] = ','.join(parts)
                        parts2 = lines[line_idx2].split(',', 9)
                        parts2[9] = self.replace_text_in_ass(original_formatted2, t2)
                        lines[line_idx2] = ','.join(parts2)
                        if progress_callback:
                            progress_callback((idx + 2) / len(subtitle_lines) * 100)
                        if self.translation_callback:
                            if self.is_translatable_text(clean_text):
                                self.translation_callback(clean_text, t1, f"{idx + 1:03d}")
                            if self.is_translatable_text(clean_text2):
                                self.translation_callback(clean_text2, t2, f"{idx + 2:03d}")
                        idx += 2
                        used_pair = True

            if not used_pair:
                # Calcular prev/next para cache contextual v2
                p_line = subtitle_lines[idx - 1][1] if idx > 0 else ""
                n_line = subtitle_lines[idx + 1][1] if idx + 1 < len(subtitle_lines) else ""
                translated = self.translate_text(
                    clean_text, target_lang,
                    context=self.translation_context,
                    prev_line=p_line, next_line=n_line
                )
                clean_translated = self._clean_ai_response(translated)
                if self.glossary_manager and hasattr(self, 'current_series_glossary'):
                    clean_translated = self.glossary_manager.apply_to_text(
                        clean_translated,
                        series_glossary=self.current_series_glossary
                    )
                translated_in_file[clean_text] = clean_translated
                unique_count += 1
                self.translation_context.append(clean_translated)
                # Usar context_window_size do profile
                ctx_max = self.profile.context_window_size if self.profile else 3
                if len(self.translation_context) > ctx_max:
                    self.translation_context.pop(0)
                parts = lines[line_idx].split(',', 9)
                parts[9] = self.replace_text_in_ass(original_formatted, clean_translated)
                lines[line_idx] = ','.join(parts)
                if progress_callback:
                    progress_callback((idx + 1) / len(subtitle_lines) * 100)
                if self.translation_callback and self.is_translatable_text(clean_text):
                    self.translation_callback(clean_text, clean_translated, f"{idx + 1:03d}")
                idx += 1

        if unique_count < len(subtitle_lines):
            self.logger.log('info', f'Deduplicação ASS: {unique_count} únicos de {len(subtitle_lines)} linhas')
        return '\n'.join(lines)
    
    def extract_text_from_ass(self, ass_line):
        """Extract readable text from ASS formatted line"""
        import re
        # Remove ASS override tags like {\fad(100,0)\blur5...}
        text = re.sub(r'\{[^}]*\}', '', ass_line)
        # Decode HTML entities
        text = html.unescape(text)
        return text.strip()
    
    def is_translatable_text(self, text):
        """Check if text is worth translating"""
        if not text or len(text.strip()) < 2:
            return False
        
        text = text.strip()
        
        # Skip pure numbers
        if text.isdigit():
            return False
        
        # Skip if mostly numbers or symbols
        alpha_chars = sum(1 for c in text if c.isalpha())
        if alpha_chars < 2:
            return False
        
        # Detect language — behaviour depends on source_lang config
        source_lang = self.keys.get('source_lang', 'auto')
        target_lang_check = self.keys.get('target_lang', 'pt-BR').lower().split('-')[0]
        try:
            import langdetect
            detected_lang = langdetect.detect(text)

            if source_lang == 'auto':
                # Auto mode: skip only if text is already in the target language
                if detected_lang == target_lang_check:
                    return False
            else:
                # Specific source: skip only if text is already in target language
                # (allow the configured source language through)
                if detected_lang == target_lang_check:
                    return False

        except (ImportError, langdetect.LangDetectError):
            pass
        
        return True
    
    def replace_text_in_ass(self, original_formatted, new_text):
        """Replace text in ASS line while preserving formatting"""
        import re
        # Find all formatting tags
        tags = re.findall(r'\{[^}]*\}', original_formatted)
        # If no tags, just return new text
        if not tags:
            return new_text
        # Reconstruct with new text
        result = ''.join(tags) + new_text
        return result

    def translate_text(self, text, target_lang, context=None, prev_line="", next_line=""):
        if self.stop_processing:
            return text
        self.translation_stats['total_translations'] += 1
        job = self.current_job

        # ── LineClassifier: classificar antes de traduzir ──
        if self.line_classifier and LineType:
            line_type, processed = self.line_classifier.classify(text)
            if job:
                type_map = {
                    LineType.DIALOGUE: 'classified_dialogue',
                    LineType.SOUND_EFFECT: 'classified_sfx',
                    LineType.MUSIC_LYRICS: 'classified_music',
                    LineType.TECHNICAL_TAG: 'classified_tag',
                    LineType.UNTRANSLATABLE: 'classified_untranslatable',
                }
                job.stats[type_map.get(line_type, 'classified_dialogue')] += 1
                job.stats['total_lines'] += 1

            if line_type == LineType.TECHNICAL_TAG:
                return text
            if line_type == LineType.UNTRANSLATABLE:
                return text
            if line_type == LineType.SOUND_EFFECT:
                self.translation_stats['successful_translations'] += 1
                return processed
            if line_type == LineType.MUSIC_LYRICS:
                # Música: manter original (ou traduzir com prompt dedicado no futuro)
                return text

        # Dictionary for very short/simple phrases that AI often fails to translate
        simple_translations = {
            'en': {
                'pt-BR': {
                    'Shit!': 'Merda!', 'Damn!': 'Droga!',
                    'Roger.': 'Entendido.', 'Roger!': 'Entendido!',
                    'Later!': 'Até mais!', 'What?!': 'O quê?!',
                    'Wha...': 'O quê...', 'Um...': 'Hum...',
                    'Uh...': 'Ah...', 'Y-Yes...': 'S-Sim...',
                    'I repeat.': 'Repito.', 'A hostage?!': 'Um refém?!',
                    "Don't......": 'Não...', "Please don't......": 'Por favor, não...',
                    "Stop it already......": 'Pare com isso...',
                }
            }
        }
        
        if target_lang in simple_translations.get('en', {}):
            simple_dict = simple_translations['en'][target_lang]
            if text.strip() in simple_dict:
                self.translation_stats['successful_translations'] += 1
                return simple_dict[text.strip()]
        
        # ── Cache check (v2 contextual) ──
        effective_source = self.keys.get('source_lang', 'auto')
        if effective_source == 'auto':
            effective_source = 'en'
        cached_result = self.cache.get(text, effective_source, target_lang,
                                        prev_line=prev_line, next_line=next_line)
        if cached_result:
            if cached_result.strip().lower() != text.strip().lower():
                self.translation_stats['cache_hits'] += 1
                if job:
                    job.stats['cache_hits'] += 1
                self.logger.log('debug', f'💾 Cache HIT | Original: "{text[:50]}..." → Traduzido: "{cached_result[:50]}..."')
                return cached_result
            else:
                self.translation_stats['identical_translations'] += 1
                self.logger.log('warning', f'⚠️ Cache RUIM (idêntico) | Retraduzindo: "{text[:80]}"')
        else:
            self.translation_stats['cache_misses'] += 1
            if job:
                job.stats['cache_misses'] += 1
        
        clean_input = text.replace('&quot;', '"').replace('&#39;', "'").replace('&amp;', '&')
        
        # ── Buscar few-shots por gênero ──
        fewshot_ex = None
        if get_fewshot_examples and job and job.series_metadata:
            meta = job.series_metadata
            fewshot_ex = get_fewshot_examples(
                series_type=meta.detect_type(),
                genres=meta.genres,
            )

        # Usar contexto do job se disponível
        effective_context = context
        if job and not effective_context:
            effective_context = job.get_recent_context()
        
        for api in [self.api_type]:
            if api not in self.translators:
                self.logger.log('warning', f'API {api} não disponível. Verifique a configuração.')
                break
            try:
                result = None

                # ── Usar PromptBuilder para APIs LLM ──
                _src_lang = self.keys.get('source_lang', 'auto')
                if _src_lang == 'auto':
                    _src_lang = 'en'
                if self.prompt_builder and api in ('Ollama', 'GPT', 'Gemini'):
                    prompt_data = self.prompt_builder.build(
                        backend=api,
                        text=clean_input,
                        job=job,
                        fewshot_examples=fewshot_ex,
                        source_lang=_src_lang,
                        target_lang=target_lang,
                    )
                    # Injetar contexto antigo no job se presente
                    if job and effective_context:
                        for ctx in effective_context:
                            if ctx not in job.translation_context:
                                job.translation_context.append(ctx)

                    if api == 'Ollama':
                        result = self._ollama_translate_v2(clean_input, target_lang, prompt_data)
                    elif api == 'GPT':
                        _temp = self.profile.temperature if self.profile else 0.3
                        _msgs = [
                            {"role": "system", "content": prompt_data['system']},
                            {"role": "user", "content": prompt_data['user']},
                        ]
                        if _OPENAI_V1:
                            _client = self.translators.get('GPT')
                            if not _client or not isinstance(_client, openai.OpenAI):
                                _client = openai.OpenAI(api_key=self.keys.get('gpt', ''))
                            response = _client.chat.completions.create(
                                model="gpt-3.5-turbo",
                                messages=_msgs,
                                temperature=_temp,
                            )
                            result = response.choices[0].message.content.strip()
                        else:
                            response = openai.ChatCompletion.create(
                                model="gpt-3.5-turbo",
                                messages=_msgs,
                                temperature=_temp,
                            )
                            result = response.choices[0].message.content.strip()
                    elif api == 'Gemini':
                        model = genai.GenerativeModel('gemini-pro')
                        prompt = f"{prompt_data['system']}\n\n{prompt_data['user']}"
                        response = model.generate_content(prompt)
                        result = response.text.strip()
                elif self.prompt_builder and api == 'DeepL':
                    prompt_data = self.prompt_builder.build(
                        backend='DeepL', text=clean_input, job=job,
                        source_lang=_src_lang, target_lang=target_lang,
                    )
                    api_result = self.translators[api].translate_text(
                        prompt_data['text'], target_lang=target_lang
                    )
                    result = api_result.text
                elif self.prompt_builder and api == 'Google':
                    prompt_data = self.prompt_builder.build(
                        backend='Google', text=clean_input, job=job,
                        source_lang=_src_lang, target_lang=target_lang,
                    )
                    api_result = self.translators[api].translate(
                        prompt_data['text'], dest=target_lang.split('-')[0]
                    )
                    result = api_result.text
                else:
                    # Fallback: APIs sem PromptBuilder
                    if api == 'Argos':
                        result = self.translators[api].translate_text(clean_input, target_lang)
                    elif api == 'CTranslate2':
                        result = self.translators[api].translate_text(clean_input, target_lang)
                    elif api == 'DeepL':
                        api_result = self.translators[api].translate_text(clean_input, target_lang=target_lang)
                        result = api_result.text
                    elif api == 'Ollama':
                        result = self._ollama_translate(clean_input, target_lang, context=effective_context)
                    elif api == 'Google':
                        api_result = self.translators[api].translate(clean_input, dest=target_lang.split('-')[0])
                        result = api_result.text
                    elif api == 'GPT':
                        response = openai.ChatCompletion.create(
                            model="gpt-3.5-turbo",
                            messages=[
                                {"role": "system", "content": self.system_prompt},
                                {"role": "user", "content": f"Translate to Portuguese: {clean_input}"}
                            ],
                            temperature=0.3
                        )
                        result = response.choices[0].message.content.strip()
                    elif api == 'Gemini':
                        model = genai.GenerativeModel('gemini-pro')
                        prompt = f"{self.system_prompt}\n\nTranslate to Portuguese: {clean_input}"
                        response = model.generate_content(prompt)
                        result = response.text.strip()
                    elif api == 'LibreTranslate':
                        result = self.translators[api].translate(clean_input, 'en', target_lang)
                
                if result and result.strip():
                    try:
                        if '├' in result or '┬' in result or 'â' in result:
                            result = result.encode('latin1').decode('utf-8')
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        pass
                    
                    cleaned_result = self._clean_ai_response(result)
                    
                    if cleaned_result.strip() != clean_input.strip():
                        # Apply glossary
                        if self.glossary_manager and hasattr(self, 'current_series_glossary'):
                            cleaned_result = self.glossary_manager.apply_to_text(
                                cleaned_result,
                                series_glossary=self.current_series_glossary
                            )
                        
                        # ── Validação de qualidade com scoring de confiança ──
                        is_valid_basic = self._is_translation_valid(clean_input, cleaned_result)
                        confidence = 1.0

                        if self._quality_validator:
                            is_valid_semantic, msg, confidence = self._quality_validator.validate_line_translation(
                                clean_input, cleaned_result
                            )
                            if not is_valid_semantic and confidence < 0.3:
                                self.translation_stats['validation_rejections'] += 1
                                if job:
                                    job.stats['validation_rejections'] += 1
                                self.logger.log('warning', f'❌ Validação semântica REJEITOU: {msg} | "{clean_input[:50]}..."')
                                continue

                        if not is_valid_basic:
                            self.translation_stats['validation_rejections'] += 1
                            self.logger.log('warning', f'❌ API {api}: Tradução REJEITADA | "{clean_input[:50]}..."')
                            continue

                        # ── Self-consistency para baixa confiança (Fase 2c) ──
                        if confidence < 0.6 and api == 'Ollama':
                            second_result = self._self_consistency_check(clean_input, cleaned_result, target_lang, job)
                            if second_result:
                                cleaned_result = second_result

                        # Cache com contexto (v2)
                        _src_for_cache = self.keys.get('source_lang', 'auto')
                        if _src_for_cache == 'auto':
                            _src_for_cache = 'en'
                        self.cache.set(text, cleaned_result, _src_for_cache, target_lang, api,
                                      prev_line=prev_line, next_line=next_line)
                        self.translation_stats['successful_translations'] += 1
                        if job:
                            job.stats['successful_translations'] += 1
                            job.add_context(cleaned_result)
                            job.track_auto_glossary(clean_input, cleaned_result)

                        self.logger.log('info', f'✅ API {api} | Original: "{clean_input[:50]}..." → Traduzido: "{cleaned_result[:50]}..."')
                        return cleaned_result
                    else:
                        self.translation_stats['identical_translations'] += 1
                        self.logger.log('warning', f'⚠️ API {api}: Tradução IDÊNTICA ao original | Texto: "{clean_input[:80]}"')
                        continue
                else:
                    self.translation_stats['api_failures'] += 1
                    self.logger.log('warning', f'⚠️ API {api}: Resultado vazio | Original: "{clean_input[:80]}"')
                    continue
                    
            except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                self.logger.log('warning', f'API {api} network error: {str(e)}')
                continue
            except Exception as e:
                error_msg = str(e).lower()
                if 'quota' in error_msg or 'limit' in error_msg or 'billing' in error_msg:
                    self.api_status[api] = 'quota_exceeded'
                    self._save_api_status()
                    self.logger.log('warning', f'API {api}: Quota excedida - marcada como indisponível')
                else:
                    self.logger.log('warning', f'API {api} falhou para texto: {str(e)}')
                continue
        
        self.translation_stats['api_failures'] += 1
        self.logger.log('error', f'🚫 API {self.api_type} falhou | Texto não traduzido: "{text[:100]}"')
        return text

    def translate_batch_optimized(self, texts, target_lang, progress_callback=None):
        if not texts:
            return []
        
        translated = []
        
        # Use configured parallelism (clamped for GPU protection)
        chunk_size = 2  # Smaller chunks
        max_workers = self.max_parallelism  # Use configured parallelism
        
        chunks = []
        for chunk_start in range(0, len(texts), chunk_size):
            chunk_end = min(chunk_start + chunk_size, len(texts))
            chunks.append((chunk_start, texts[chunk_start:chunk_end]))
        
        # Process chunks sequentially for better control
        for chunk_start, chunk in chunks:
            # Check for stop signal
            if hasattr(self, 'stop_processing') and self.stop_processing:
                self.logger.log('info', 'Processamento interrompido pelo usuário')
                break
            
            try:
                chunk_translated = self._translate_chunk(chunk, target_lang)
                translated.extend(chunk_translated)
                
                # Update progress
                if progress_callback:
                    completed_chunks = len([c for c in chunks if chunks.index((chunk_start, chunk)) <= chunks.index((chunk_start, chunk))])
                    progress_callback((completed_chunks / len(chunks)) * 100)
                
                # Update translation callback
                for i, (original, translation) in enumerate(zip(chunk, chunk_translated)):
                    if self.translation_callback and self.is_translatable_text(original):
                        timestamp = f"{chunk_start + i + 1:03d}"
                        self.translation_callback(original, translation, timestamp)
                
            except Exception as e:
                self.logger.log('error', f'Erro no chunk {chunk_start}: {e}')
                # Add fallback translations
                translated.extend(chunk)
        
        return translated
    
    def _translate_chunk(self, texts, target_lang):
        """Translate a small chunk of texts. Para Ollama usa translate_text (cache + mesmo prompt que ASS)."""
        results = []
        for text in texts:
            if self.stop_processing:
                results.append(text)
                continue
            results.append(self.translate_text(text, target_lang) if self.is_translatable_text(text) else text)
        return results
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.Timeout))
    )
    def _ollama_request_with_retry(self, url, payload, timeout=60):
        """Make Ollama request with retry logic"""
        return requests.post(url, json=payload, timeout=timeout)
    
    def _ollama_translate_chunk(self, texts, target_lang):
        """Translate chunk using Ollama with better stop control"""
        try:
            if 'Ollama' not in self.translators or not requests:
                return texts
            
            # Check for stop signal before starting
            if hasattr(self, 'stop_processing') and self.stop_processing:
                return texts
            
            config = self.translators['Ollama']
            url = config['url'].rstrip('/')
            model = config['model']
            
            # Prepare texts for translation
            translatable_texts = []
            text_indices = []
            
            for i, text in enumerate(texts):
                if self.is_translatable_text(text):
                    translatable_texts.append(text)
                    text_indices.append(i)
            
            if not translatable_texts:
                return texts
            
            # Process each text individually for better stop control
            result_texts = texts.copy()
            
            for idx, (text_idx, text) in enumerate(zip(text_indices, translatable_texts)):
                # Check stop signal before each translation
                if hasattr(self, 'stop_processing') and self.stop_processing:
                    self.logger.log('info', 'Tradução interrompida')
                    break
                
                # Simple translation prompt
                user_message = f"Translate to natural Brazilian Portuguese: {text}"
                
                perf_opts = self.profile._perf_options() if self.profile else {"num_ctx": 2048, "num_batch": 512}
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are a professional subtitle translator. Translate to natural Brazilian Portuguese. Avoid excessive use of 'né', 'tá'. Use formal Portuguese when appropriate. Return only the translation."},
                        {"role": "user", "content": user_message}
                    ],
                    "stream": False,
                    "keep_alive": "30m",
                    "options": {
                        "temperature": 0.2,
                        "top_p": 0.8,
                        "num_predict": 80,
                        "repeat_penalty": 1.2,
                        **perf_opts,
                    }
                }
                
                try:
                    response = requests.post(
                        f'{url}/api/chat', 
                        json=payload, 
                        timeout=30  # Shorter timeout
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        translation = result['message']['content'].strip()
                        
                        # Clean translation with enhanced rules
                        translation = self._clean_ai_response(translation)
                        
                        # Additional quality check for Ollama
                        if self._is_quality_translation(text, translation):
                            result_texts[text_idx] = translation
                        else:
                            result_texts[text_idx] = text
                    
                except Exception as e:
                    self.logger.log('warning', f'Ollama falhou para texto: {e}')
                    continue
            
            return result_texts
                
        except Exception as e:
            self.logger.log('error', f'Ollama chunk translation failed: {e}')
            return self._fallback_translate_chunk(texts, target_lang)
    
    def _parse_numbered_translations(self, text, expected_count):
        """Parse numbered translations from Ollama response"""
        translations = []
        lines = text.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Look for numbered format: "1. Translation" or "1) Translation"
            match = re.match(r'^(\d+)[.)\s]+(.+)$', line)
            if match:
                translation = match.group(2).strip()
                # Clean basic formatting
                if translation.startswith('"') and translation.endswith('"'):
                    translation = translation[1:-1]
                translations.append(translation)
        
        # Ensure we have the expected number of translations
        while len(translations) < expected_count:
            translations.append("[ERRO]")
        
        return translations[:expected_count]
    
    def _fallback_translate_chunk(self, texts, target_lang):
        """Fallback to Google/DeepL for chunk translation"""
        result_texts = []
        
        for text in texts:
            if self.is_translatable_text(text):
                # Try Google first, then DeepL
                if 'Google' in self.translators:
                    try:
                        result = self.translators['Google'].translate(text, dest='pt')
                        result_texts.append(result.text)
                        continue
                    except Exception as e:
                        self.logger.log('debug', f'Google fallback falhou: {e}')
                
                if 'DeepL' in self.translators:
                    try:
                        result = self.translators['DeepL'].translate_text(text, target_lang='pt')
                        result_texts.append(result.text)
                        continue
                    except Exception as e:
                        self.logger.log('debug', f'DeepL fallback falhou: {e}')
                
                # If all fail, keep original
                result_texts.append(text)
            else:
                result_texts.append(text)
        
        return result_texts
    
    def translate_batch_with_context(self, subtitle_lines, batch_size=6, context_lines=2, target_lang='pt-BR'):
        """
        Translate subtitle lines in batches with context for improved coherence.
        
        Args:
            subtitle_lines: List of tuples (line_idx, original_text, formatted_text)
            batch_size: Number of lines to translate per batch (4-10)
            context_lines: Number of previous lines to include as context (1-3)
            target_lang: Target language code
            
        Returns:
            List of translated texts in the same order
        """
        if not subtitle_lines:
            return []
        
        batch_size = max(4, min(10, batch_size))  # Clamp to 4-10
        context_lines = max(1, min(3, context_lines))  # Clamp to 1-3
        
        all_translations = [''] * len(subtitle_lines)
        context_buffer = []  # Store recent translations for context
        
        # Process in batches
        for batch_start in range(0, len(subtitle_lines), batch_size):
            if self.stop_processing:
                break
            
            batch_end = min(batch_start + batch_size, len(subtitle_lines))
            current_batch = subtitle_lines[batch_start:batch_end]
            
            # Extract texts to translate
            batch_texts = [line[1] for line in current_batch]  # original_text
            
            # Try batch translation with context
            try:
                translated_batch = self._translate_batch_with_context_ollama(
                    batch_texts,
                    context_buffer[-context_lines:] if context_buffer else [],
                    target_lang
                )
                
                # If batch succeeded, store results
                if translated_batch and len(translated_batch) == len(batch_texts):
                    for i, (line_idx, _, _) in enumerate(current_batch):
                        all_translations[line_idx] = translated_batch[i]
                        context_buffer.append(translated_batch[i])
                else:
                    # Fallback to line-by-line
                    self.logger.log('warning', f'Batch translation failed, falling back to line-by-line')
                    for i, (line_idx, original_text, _) in enumerate(current_batch):
                        translation = self.translate_text(original_text, target_lang)
                        all_translations[line_idx] = translation
                        context_buffer.append(translation)
                        
            except Exception as e:
                self.logger.log('error', f'Batch translation error: {e}, falling back')
                # Fallback to line-by-line
                for i, (line_idx, original_text, _) in enumerate(current_batch):
                    translation = self.translate_text(original_text, target_lang)
                    all_translations[line_idx] = translation
                    context_buffer.append(translation)
            
            # Keep context buffer manageable
            if len(context_buffer) > context_lines * 3:
                context_buffer = context_buffer[-context_lines * 3:]
        
        return all_translations
    
    def _translate_batch_with_context_ollama(self, batch_texts, context_texts, target_lang):
        """
        Translate a batch of texts with context using Ollama.
        Usa PromptBuilder quando disponível.
        """
        if self.stop_processing:
            return batch_texts
        if 'Ollama' not in self.translators:
            self.logger.log('error', 'Ollama not in translators dict')
            return None
        
        if not requests:
            self.logger.log('error', 'requests module not available')
            return None
        
        config = self.translators['Ollama']
        url = config['url'].rstrip('/')
        model = config['model']
        
        self.logger.log('debug', f'Batch translation: {len(batch_texts)} texts with Ollama')

        # ── Usar PromptBuilder se disponível ──
        job = self.current_job
        if self.prompt_builder and job:
            # Injetar contexto no job temporariamente
            old_ctx = job.translation_context[:]
            if context_texts:
                for ct in context_texts:
                    if ct not in job.translation_context:
                        job.translation_context.append(ct)

            prompt_data = self.prompt_builder.build_batch(
                backend='Ollama',
                texts=batch_texts,
                job=job,
                use_batch_prompt=getattr(self, 'use_batch_prompt', False),
            )
            system_content = prompt_data['system']
            user_message = prompt_data['user']
            options = prompt_data['options']

            # Restaurar contexto
            job.translation_context = old_ctx
        else:
            # Fallback: construir manualmente (comportamento anterior)
            N = len(batch_texts)
            numbered_batch = "\n".join(f"{i+1}│ {t}" for i, t in enumerate(batch_texts))

            context_section = ""
            if context_texts:
                context_section = "Contexto anterior (use para manter consistência, mas NÃO traduza):\n"
                for i, ctx in enumerate(context_texts[-3:], 1):
                    context_section += f"  [{i}] {ctx}\n"
                context_section += "\n"

            glossary_section = ""
            if self.glossary_manager and hasattr(self, 'current_series_glossary'):
                glossary_injection = self.glossary_manager.get_prompt_injection(
                    series_glossary=self.current_series_glossary
                )
                if glossary_injection:
                    glossary_section = f"\n{glossary_injection}\n"

            if getattr(self, 'use_batch_prompt', False):
                system_content = getattr(self, 'system_prompt_batch', self.system_prompt)
                example_lines = "\n".join([f"{i}│ ..." for i in range(1, min(N, 4) + 1)])
                if N > 4:
                    example_lines += f"\n...\n{N}│ ..."
                user_message = (
                    f"{context_section}Traduza as {N} linhas abaixo para português do Brasil. "
                    f"Responda SOMENTE com {N} linhas no formato:\n{example_lines}\n\n"
                    f"ENTRADA ({N} linhas):\n{numbered_batch}{glossary_section}"
                )
            else:
                system_content = self.system_prompt
                user_message = (
                    f"{context_section}Agora traduza APENAS as linhas numeradas abaixo para português brasileiro natural, "
                    f"mantendo tom, gírias e continuidade do diálogo.\n"
                    f"Responda SOMENTE com as linhas traduzidas no mesmo formato numerado (1│ tradução):\n\n"
                    f"{numbered_batch}\nRegras:\n"
                    f"- SOMENTE a tradução das linhas numeradas (formato: 1│ texto traduzido)\n"
                    f"- Preserve tags ASS/SSA como {{\\i1}}, {{\\an8}}, etc.\n"
                    f"- Não traduza nomes próprios, efeitos sonoros (*suspiro*), onomatopeias\n"
                    f"- Use pronomes e tom consistentes com o contexto anterior{glossary_section}"
                )
            options = self.profile.get_batch_ollama_options(len(numbered_batch)) if self.profile else {
                "temperature": 0.3, "top_p": 0.85,
                "num_predict": min(2200, len(numbered_batch) * 3),
                "repeat_penalty": 1.15,
            }

        perf_opts = self.profile._perf_options() if self.profile else {"num_ctx": 2048, "num_batch": 512}
        options.update(perf_opts)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_message}
            ],
            "stream": False,
            "keep_alive": "30m",
            "options": options,
        }
        
        try:
            response = requests.post(
                f'{url}/api/chat',
                json=payload,
                timeout=120  # Longer timeout for batch
            )
            
            if response.status_code == 200:
                result = response.json()
                raw_response = result['message']['content'].strip()
                
                # Parse numbered response
                translations = self._parse_numbered_batch_response(raw_response, len(batch_texts))
                
                if translations is None:
                    self.logger.log('error', 'Batch parsing failed completely')
                    return None
                
                if len(translations) == len(batch_texts):
                    # Clean and apply glossary to each translation
                    cleaned_translations = []
                    has_none = False
                    
                    for i, trans in enumerate(translations):
                        if trans is None:
                            # Use original text for missing translations
                            cleaned_translations.append(batch_texts[i])
                            has_none = True
                        else:
                            clean_trans = self._clean_ai_response(trans)
                            
                            # Validate translation actually changed
                            if clean_trans.strip() == batch_texts[i].strip():
                                self.logger.log('warning', f'Line {i+1} was not translated')
                            
                            # Apply glossary if available
                            if self.glossary_manager and hasattr(self, 'current_series_glossary'):
                                clean_trans = self.glossary_manager.apply_to_text(
                                    clean_trans,
                                    series_glossary=self.current_series_glossary
                                )
                            
                            cleaned_translations.append(clean_trans)
                    
                    # If any translations are missing, log warning
                    if has_none:
                        self.logger.log('warning', 'Some translations in batch were missing, used originals')
                    
                    return cleaned_translations
                else:
                    self.logger.log('error', f'Expected {len(batch_texts)} translations, got {len(translations)}')
                    return None
            else:
                self.logger.log('error', f'Ollama batch request failed: {response.status_code}')
                return None
                
        except Exception as e:
            self.logger.log('error', f'Ollama batch translation error: {e}')
            return None
    
    def _parse_numbered_batch_response(self, response_text, expected_count):
        """
        Parse numbered batch response from Ollama.
        Expected format: 
        1│ translated text
        2│ another translation
        ...
        
        Returns list of translations in order, or None if parsing fails badly.
        """
        translations = {}
        lines = response_text.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Match numbered format: "1│ text" or "1. text" or "1) text" or "1: text"
            # Also try to match without separator if line starts with number
            match = re.match(r'^(\d+)[│.\):\s]+(.+)$', line, re.DOTALL)
            if not match:
                # Try alternative: "1 - text" or just "1 text"
                match = re.match(r'^(\d+)\s*[-–—]?\s+(.+)$', line, re.DOTALL)
            
            if match:
                num = int(match.group(1))
                translation = match.group(2).strip()
                
                # Remove quotes if present
                if translation.startswith('"') and translation.endswith('"'):
                    translation = translation[1:-1]
                if translation.startswith("'") and translation.endswith("'"):
                    translation = translation[1:-1]
                
                # Skip if translation is empty or just punctuation
                if translation and not translation.replace('.', '').replace(',', '').strip() == '':
                    translations[num] = translation
        
        # Check if we got enough translations (at least 60%)
        if len(translations) < expected_count * 0.6:
            self.logger.log('error', f'Batch parse failed: only {len(translations)}/{expected_count} translations found')
            return None
        
        # Build ordered list
        result = []
        missing_count = 0
        for i in range(1, expected_count + 1):
            if i in translations:
                result.append(translations[i])
            else:
                # Missing translation - return None to trigger fallback
                missing_count += 1
                self.logger.log('warning', f'Missing translation for line {i}')
                result.append(None)  # Use None instead of error string
        
        # If too many missing, return None to trigger line-by-line fallback
        if missing_count > expected_count * 0.3:  # More than 30% missing
            self.logger.log('error', f'Too many missing translations ({missing_count}/{expected_count}), triggering fallback')
            return None
        
        return result
    
    def stop_translation(self):
        """Stop the translation process immediately"""
        self.stop_processing = True
        self.logger.log('info', '🛑 Sinal de parada enviado para o tradutor')
    
    def _quick_quality_check(self, original, translated):
        """Fast quality check for batch processing"""
        if not translated or translated.strip() == original.strip():
            return False
        
        # Clean the translation first
        clean_translated = self._clean_ai_response(translated)
        
        # Apply glossary if available
        if self.glossary_manager and hasattr(self, 'current_series_glossary'):
            clean_translated = self.glossary_manager.apply_to_text(
                clean_translated,
                series_glossary=self.current_series_glossary
            )
        
        if not clean_translated or len(clean_translated) < len(original) * 0.2:
            return False
        
        # Check for repetition patterns
        if len(clean_translated) > 10:
            # Check for character repetition like "ptptptpt"
            for i in range(len(clean_translated) - 3):
                pattern = clean_translated[i:i+2]
                if pattern * 5 in clean_translated:
                    return False
        
        # Check for AI explanation indicators
        ai_indicators = ['Note:', 'Se você quiser', 'Esta é uma', 'No entanto', 'Define']
        if any(indicator in clean_translated for indicator in ai_indicators):
            return False
        
        return True
    def _clean_ai_response(self, text):
        """Enhanced cleaning for AI responses - removes explanations, prompt leakage, non-Portuguese content"""
        if not text:
            return text
        
        # Don't clean timestamps or numbers
        if '-->' in text or re.match(r'^\d+$', text.strip()):
            return text
        
        # Basic cleaning
        text = text.strip()
        
        # Remove prompt leakage (model echoing instructions)
        text = re.sub(r'\s*Previous context \(read only, do NOT translate\):.*$', '', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'^\s*Previous context \(read only, do NOT translate\):\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*Previous context \(read only, do NOT translate\):\s*', ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*\(Brazil\)\s*', ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'^\s*\(Brazil\)\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'^\s*Portuguese\s*\(FULL translation\):\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'^\s*Portuguese:\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\bai\s+\(Brazil\)\s*', '', text, flags=re.IGNORECASE)
        # Remove "English: ..." remnants when model repeats the prompt
        text = re.sub(r'\s*English:.*$', '', text, flags=re.IGNORECASE)
        text = text.strip()
        
        # If response looks like an explanation (tradutor, glossário, contexto, etc.) and is long, keep only the actual translation
        t_lower = text.lower()
        if len(text) > 120 and any(x in t_lower for x in ('tradutor', 'glossário', 'contexto', 'nesta linha', 'tradução mais natural')):
            # Prefer a very short sentence (likely the real translation buried in the explanation)
            short_sentences = re.findall(r'[^.!?\n]{1,40}[.!?]', text)
            for s in short_sentences:
                s = s.strip()
                if len(s) <= 25 and not any(y in s.lower() for y in ('tradutor', 'glossário', 'contexto', 'termos', 'parabéns', 'acertou')):
                    text = s
                    break
            else:
                # Else take first sentence that isn't explanation
                for sep in ('. ', '! ', '? ', '\n'):
                    parts = text.split(sep, 1)
                    if len(parts) >= 2:
                        first = (parts[0] + sep.replace('\n', '')).strip()
                        if len(first) <= 80 and not any(y in first.lower() for y in ('tradutor', 'glossário', 'contexto', 'termos')):
                            text = first
                            break
                else:
                    m = re.match(r'^([^.!?\n]{1,80}[.!?])', text)
                    if m and not any(z in m.group(1).lower() for z in ('tradutor', 'glossário', 'contexto')):
                        text = m.group(1).strip()
        text = text.strip()
        
        # Fix HTML entities first
        text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&amp;', '&')
        text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ')
        
        # CRITICAL: Remove Japanese/Chinese punctuation that shouldn't be in Portuguese
        text = text.replace('。', '.')  # Japanese period
        text = text.replace('、', ',')  # Japanese comma
        text = text.replace('！', '!')  # Fullwidth exclamation
        text = text.replace('？', '?')  # Fullwidth question mark
        text = text.replace('…', '...')  # Horizontal ellipsis
        
        # Fix excessive ellipsis (more than 3 dots) - normalize to single ellipsis
        text = re.sub(r'\.{4,}', '...', text)  # Replace 4+ consecutive dots with exactly 3
        text = re.sub(r'(\.{3,}\s*){2,}', '...', text)  # Replace multiple ellipsis groups with one
        
        # CRITICAL: Remove explanations and notes that models add
        # Remove content in parentheses that looks like explanations
        text = re.sub(r'\(Note that.*?\)', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'\(ou seja.*?\)', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'\(observe que.*?\)', '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # Remove content in square brackets that looks like notes
        text = re.sub(r'\[.*?tradução.*?\]', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'\[.*?português.*?\]', '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # CRITICAL: Remove Chinese/Japanese characters and associated explanations
        # First remove pure Chinese character sequences
        text = re.sub(r'[\u4e00-\u9fff]+', '', text)  # Remove Chinese characters
        text = re.sub(r'[\u3040-\u309f\u30a0-\u30ff]+', '', text)  # Remove Japanese hiragana/katakana
        
        # Remove lines that contain Chinese language markers
        lines = text.split('。')
        cleaned_lines = []
        for line in lines:
            if not re.search(r'[\u4e00-\u9fff]|请注意|在此|根据|特定词汇', line):
                cleaned_lines.append(line)
        text = '。'.join(cleaned_lines)
        
        # Remove wrapping quotes
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        
        # Fix common grammar errors
        text = text.replace('foresse', 'fosse')
        
        # Fix excessive colloquialisms
        text = re.sub(r'(, né\?|, tá\?)', '', text)  # Remove excessive né/tá at end
        text = re.sub(r'\btá legalize\?', 'certo?', text)  # Fix weird translations
        text = re.sub(r'\bEm onde\b', 'Onde', text)  # Fix "Em onde" -> "Onde"
        text = re.sub(r'\bno amor com\b', 'apaixonado por', text)  # Fix love expressions
        
        # Fix incomplete Japanese translations
        if text.endswith(' naquela') or text.endswith(' naquele'):
            text = text.replace(' naquela', ' naquela hora').replace(' naquele', ' naquele momento')
        
        # CRITICAL: Detect and fix truncated/corrupted text
        # If text ends with incomplete word or looks corrupted, it's a bad translation
        if re.search(r'[a-zA-Z]{15,}$', text):  # Long string without spaces at end = corrupted
            # Text is corrupted, return empty to force retry
            return ''
        
        # Clean up excess whitespace (including double spaces)
        text = re.sub(r'\s{2,}', ' ', text)  # Replace 2+ spaces with single space
        text = text.strip()
        
        return text
    
    def _extract_series_id_from_path(self, file_path: str):
        """
        Extract series ID from subtitle file path.
        Looks for tvdbid=XXXXX pattern in path.
        
        Returns:
            Series ID if found, None otherwise
        """
        try:
            # Look for tvdbid=XXXXX pattern
            match = re.search(r'tvdbid[=_](\d+)', file_path, re.IGNORECASE)
            if match:
                return int(match.group(1))
            
            # Alternative: look for numeric folder pattern (fallback)
            # Example: /123456/ or -(123456)-
            match = re.search(r'[\\/\\-](\d{6,8})[\\/\\-]', file_path)
            if match:
                return int(match.group(1))
            
            self.logger.log('debug', f'Não conseguiu extrair series_id do caminho: {file_path}')
            return None
        except Exception as e:
            self.logger.log('warn', f'Erro ao extrair series_id: {e}')
            return None
    
    def _ollama_translate(self, text, target_lang, context=None):
        """Ollama translation with glossary injection and optional context. Uma retentativa em caso de timeout."""
        if 'Ollama' not in self.translators or not requests:
            self.logger.log('warning', 'Ollama not available for translation')
            return text

        config = self.translators['Ollama']
        url = config['url'].rstrip('/')
        model = config['model']

        # Create system prompt with glossary injection
        system_prompt = self.system_prompt
        if self.glossary_manager and hasattr(self, 'current_series_glossary'):
            glossary_injection = self.glossary_manager.get_prompt_injection(
                series_glossary=self.current_series_glossary
            )
            system_prompt = f"{system_prompt}\n\n{glossary_injection}"

        # Build prompt with optional context
        if context and len(context) > 0:
            context_lines = "\n".join([f"[{i+1}] {ctx}" for i, ctx in enumerate(context[-2:])])
            prompt = f"""Previous context (read only, do NOT translate):
{context_lines}

TRANSLATE the line below from English to Portuguese (Brazil).
IMPORTANT: You MUST translate it. Do NOT return the original English text.
RESPOND WITH ONLY THE TRANSLATION in Portuguese. NO explanations. NO notes.

English: {text}
Portuguese:"""
        else:
            prompt = f"""Translate COMPLETELY from English to Portuguese (Brazil).
Translate the ENTIRE sentence below - ALL words.

English: {text}
Portuguese (FULL translation):"""

        perf_opts = self.profile._perf_options() if self.profile else {"num_ctx": 2048, "num_batch": 512}
        payload = {
            "model": model,
            "prompt": prompt,
            "system": system_prompt,
            "stream": False,
            "keep_alive": "30m",
            "options": {
                "temperature": 0.5,
                "top_p": 0.9,
                "repeat_penalty": 1.3,
                "num_predict": 50,
                "stop": ["\n", "\\n", "Nota:", "Note:", "explain", "explicar", "English:", "Inglês:", "Previous context"],
                **perf_opts,
            }
        }

        timeout_sec = 120  # Cold start / GPU ocupada podem demorar
        for attempt in range(2):
            try:
                response = requests.post(
                    f'{url}/api/generate',
                    json=payload,
                    timeout=timeout_sec
                )

                if response.status_code == 200:
                    result = response.json()
                    translation = result.get('response', '').strip()

                    if not translation:
                        self.logger.log('warning', f'Ollama retornou resposta vazia para: "{text[:30]}..."')
                        return text

                    try:
                        if '├' in translation or '┬' in translation or 'â' in translation:
                            translation = translation.encode('latin1').decode('utf-8')
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        pass

                    if translation.startswith('"') and translation.endswith('"'):
                        translation = translation[1:-1]
                    translation = translation.replace('foresse', 'fosse')
                    final_trans = translation.strip() if translation.strip() else text

                    if final_trans != text:
                        self.logger.log('debug', f'Ollama OK: "{text[:20]}..." -> "{final_trans[:20]}..."')
                    else:
                        self.logger.log('warning', f'Ollama não mudou: "{text[:30]}..."')
                    return final_trans
                else:
                    self.logger.log('error', f'Ollama HTTP error {response.status_code}: {response.text[:100]}')
                    return text

            except requests.exceptions.Timeout:
                if attempt == 0:
                    self.logger.log('warning', f'Ollama timeout ({timeout_sec}s), tentando novamente...')
                else:
                    self.logger.log('warning', f'Ollama timeout para: "{text[:30]}..."')
                    return text
            except Exception as e:
                self.logger.log('error', f'Ollama error: {str(e)[:100]} para texto: "{text[:30]}..."')
                return text

        return text

    def _ollama_translate_v2(self, text, target_lang, prompt_data):
        """
        Ollama translation v2 usando PromptBuilder.
        prompt_data contém: system, user, options (com stop tokens).
        """
        if 'Ollama' not in self.translators or not requests:
            return text

        config = self.translators['Ollama']
        url = config['url'].rstrip('/')
        model = config['model']

        perf_opts = self.profile._perf_options() if self.profile else {"num_ctx": 2048, "num_batch": 512}
        v2_options = prompt_data.get('options', {})
        v2_options.update(perf_opts)
        payload = {
            "model": model,
            "prompt": prompt_data['user'],
            "system": prompt_data['system'],
            "stream": False,
            "keep_alive": "30m",
            "options": v2_options
        }

        timeout_sec = 120
        for attempt in range(2):
            try:
                response = requests.post(
                    f'{url}/api/generate',
                    json=payload,
                    timeout=timeout_sec
                )
                if response.status_code == 200:
                    result = response.json()
                    translation = result.get('response', '').strip()
                    if not translation:
                        self.logger.log('warning', f'Ollama v2 resposta vazia: "{text[:30]}..."')
                        return text
                    try:
                        if '├' in translation or '┬' in translation or 'â' in translation:
                            translation = translation.encode('latin1').decode('utf-8')
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        pass
                    if translation.startswith('"') and translation.endswith('"'):
                        translation = translation[1:-1]
                    translation = translation.replace('foresse', 'fosse')
                    final = translation.strip() if translation.strip() else text
                    return final
                else:
                    self.logger.log('error', f'Ollama v2 HTTP {response.status_code}')
                    return text
            except requests.exceptions.Timeout:
                if attempt == 0:
                    self.logger.log('warning', f'Ollama v2 timeout, tentando novamente...')
                else:
                    return text
            except Exception as e:
                self.logger.log('error', f'Ollama v2 error: {str(e)[:100]}')
                return text
        return text

    def _self_consistency_check(self, original, first_translation, target_lang, job=None):
        """
        Self-consistency: re-traduz com temperatura mais alta e compara.
        Se ambas concordam, usa a primeira. Se divergem muito, retorna a mais curta
        (geralmente mais precisa para legendas).
        """
        if job:
            job.stats['self_consistency_triggered'] += 1

        try:
            # Segunda tradução com temperatura diferente
            if self.prompt_builder and self.profile:
                import copy
                profile2 = copy.copy(self.profile)
                profile2.temperature = min(0.7, self.profile.temperature + 0.3)
                builder2 = PromptBuilder(profile=profile2, glossary_manager=self.glossary_manager)
                prompt_data = builder2.build(
                    backend=self.api_type,
                    text=original,
                    job=job,
                )
                second = self._ollama_translate_v2(original, target_lang, prompt_data)
            else:
                second = self._ollama_translate(original, target_lang)

            if not second or second.strip() == original.strip():
                return first_translation

            # Comparar: se muito similares, usar primeira
            first_lower = first_translation.strip().lower()
            second_lower = second.strip().lower()

            if first_lower == second_lower:
                return first_translation

            # Se divergem, usar a mais curta (menos prolixa)
            if len(second.strip()) < len(first_translation.strip()) * 0.8:
                self.logger.log('debug', f'Self-consistency: usando segunda tradução (mais concisa)')
                return second.strip()

            return first_translation

        except Exception as e:
            self.logger.log('debug', f'Self-consistency falhou: {e}')
            return first_translation

    def translate_batch(self, texts, target_lang):
        return self.translate_batch_optimized(texts, target_lang)
    
    def _is_quality_translation(self, original, translated):
        """Check if Ollama translation meets quality standards"""
        if not translated or translated.strip() == original.strip():
            return False
        
        # Check for weird phrases that indicate poor translation
        weird_phrases = [
            'tá legalize', 'em onde', 'no amor com', 'foi predeterminado... né',
            'aquele/aí', 'todo ao redor', 'né?' * 3  # Too many né
        ]
        
        translated_lower = translated.lower()
        for phrase in weird_phrases:
            if phrase in translated_lower:
                return False
        
        # Check for excessive colloquialisms (relaxed: up to 40%)
        colloquial_words = {'né', 'tá', 'tipo', 'mano', 'véi', 'cara', 'tô', 'cê', 'pra'}
        words = translated_lower.split()
        if words:
            colloquial_count = sum(1 for w in words if w in colloquial_words)
            if colloquial_count / len(words) > 0.4:
                return False
        
        return True
    
    def _is_translation_valid(self, original, translated):
        """Quick validation to check if translation is reasonable"""
        if not translated or translated.strip() == original.strip():
            return False
        
        # CRITICAL: Check for Chinese/Japanese characters (signs of broken translation)
        if re.search(r'[\u4e00-\u9fff]', translated):  # Chinese characters
            self.logger.log('warning', f'⚠️ Validação REJEITADA: Caracteres chineses | Original: "{original[:60]}..." → Traduzido: "{translated[:60]}..."')
            return False
        
        if re.search(r'[\u3040-\u309f\u30a0-\u30ff]', translated):  # Japanese hiragana/katakana
            self.logger.log('warning', f'⚠️ Validação REJEITADA: Caracteres japoneses | Original: "{original[:60]}..." → Traduzido: "{translated[:60]}..."')
            return False
        
        # SIMPLIFIED: Only reject if contains HTML entities or Chinese/Japanese punctuation
        # All other validations are TOO RESTRICTIVE and reject valid Portuguese
        
        # Check for HTML entities
        if re.search(r'&\w+;', translated.strip()):
            self.logger.log('warning', f'⚠️ Validação REJEITADA: HTML entities | Original: "{original[:60]}..." → Traduzido: "{translated[:60]}..."')
            return False
        
        # Check for Chinese/Japanese punctuation that shouldn't be in Portuguese
        if re.search(r'[。、]', translated.strip()):
            self.logger.log('warning', f'⚠️ Validação REJEITADA: Pontuação asiática | Original: "{original[:60]}..." → Traduzido: "{translated[:60]}..."')
            return False
        
        # Check minimum length ratio - VERY lenient to avoid false rejections
        # Portuguese can be significantly shorter than English in many cases
        min_ratio = 0.10  # Accept if at least 10% of original length
        if len(translated.strip()) < len(original.strip()) * min_ratio:
            self.logger.log('warning', f'⚠️ Validação REJEITADA: Tradução muito curta ({len(translated)}/{len(original)} chars) | Original: "{original[:60]}..." → Traduzido: "{translated[:60]}..."')
            return False
        
        return True
    
    def _log_translation_stats(self):
        """Log detailed translation statistics summary"""
        stats = self.translation_stats
        total = stats['total_translations']
        
        if total == 0:
            return
        
        self.logger.log('info', '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        self.logger.log('info', '📊 ESTATÍSTICAS DE TRADUÇÃO:')
        self.logger.log('info', f'   Total de traduções solicitadas: {total}')
        self.logger.log('info', f'   ✅ Traduções bem-sucedidas: {stats["successful_translations"]} ({stats["successful_translations"]/total*100:.1f}%)')
        self.logger.log('info', f'   💾 Cache hits (reutilizadas): {stats["cache_hits"]} ({stats["cache_hits"]/total*100:.1f}%)')
        self.logger.log('info', f'   🔍 Cache misses (novas): {stats["cache_misses"]} ({stats["cache_misses"]/total*100:.1f}%)')
        self.logger.log('info', f'   ❌ Validações rejeitadas: {stats["validation_rejections"]} ({stats["validation_rejections"]/total*100:.1f}%)')
        self.logger.log('info', f'   ⚠️ Traduções idênticas ao original: {stats["identical_translations"]} ({stats["identical_translations"]/total*100:.1f}%)')
        self.logger.log('info', f'   🚫 Falhas de API: {stats["api_failures"]} ({stats["api_failures"]/total*100:.1f}%)')
        
        # Calculate quality score
        quality_score = (stats["successful_translations"] / total * 100) if total > 0 else 0
        
        if quality_score >= 95:
            quality_emoji = '🌟'
            quality_text = 'EXCELENTE'
        elif quality_score >= 85:
            quality_emoji = '✅'
            quality_text = 'BOM'
        elif quality_score >= 70:
            quality_emoji = '⚠️'
            quality_text = 'REGULAR'
        else:
            quality_emoji = '❌'
            quality_text = 'RUIM'
        
        self.logger.log('info', f'   {quality_emoji} Taxa de qualidade: {quality_score:.1f}% ({quality_text})')
        self.logger.log('info', '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')