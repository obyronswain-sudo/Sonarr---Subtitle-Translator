"""
PromptBuilder + TranslationProfile + TranslationJob

Responsável por montar system prompt + user message para qualquer backend.
Aplica budget de tokens com prioridade: glossário > contexto > few-shots > metadados.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class TranslationProfile:
    """Perfil centralizado de parâmetros de tradução."""
    temperature: float = 0.3
    top_p: float = 0.85
    repeat_penalty: float = 1.15
    num_predict: int = 80
    context_window_size: int = 5
    batch_size: int = 1
    max_tokens_budget: int = 2048
    # Ollama performance parameters
    num_ctx: int = 2048
    num_thread: int = 0
    # Feature flags
    enable_contextual_prompt: bool = True
    enable_fewshot: bool = True
    enable_batch_mode: bool = False
    enable_auto_glossary: bool = True

    @classmethod
    def from_config(cls, config: dict) -> 'TranslationProfile':
        """Cria perfil a partir de config.json."""
        return cls(
            temperature=float(config.get('translation_temperature', 0.3)),
            top_p=float(config.get('translation_top_p', 0.85)),
            repeat_penalty=float(config.get('translation_repeat_penalty', 1.15)),
            num_predict=int(config.get('translation_num_predict', 80)),
            context_window_size=int(config.get('context_window_size', 5)),
            batch_size=int(config.get('ass_batch_size', 1)),
            max_tokens_budget=int(config.get('max_tokens_budget', 2048)),
            num_ctx=int(config.get('num_ctx', 2048)),
            num_thread=int(config.get('num_thread', 0)),
            enable_contextual_prompt=bool(config.get('enable_contextual_prompt', True)),
            enable_fewshot=bool(config.get('enable_fewshot', True)),
            enable_batch_mode=bool(config.get('enable_batch_mode', False)),
            enable_auto_glossary=bool(config.get('enable_auto_glossary', True)),
        )

    def _perf_options(self) -> dict:
        """Parametros de performance do Ollama (num_ctx, num_thread)."""
        opts = {"num_ctx": self.num_ctx, "num_batch": 512}
        if self.num_thread > 0:
            opts["num_thread"] = self.num_thread
        return opts

    def get_ollama_options(self, text_length: int = 0) -> dict:
        """Retorna dict de options para payload Ollama."""
        predict = self.num_predict
        if text_length > 0:
            predict = max(predict, min(self.max_tokens_budget, text_length * 3))
        opts = {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "repeat_penalty": self.repeat_penalty,
            "num_predict": predict,
        }
        opts.update(self._perf_options())
        return opts

    def get_batch_ollama_options(self, batch_text_length: int) -> dict:
        """Options para modo batch."""
        opts = {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "repeat_penalty": self.repeat_penalty,
            "num_predict": min(self.max_tokens_budget, max(200, batch_text_length * 3)),
        }
        opts.update(self._perf_options())
        return opts


@dataclass
class SeriesMetadata:
    """Metadados da série para enriquecer o prompt."""
    title: str = ""
    genres: List[str] = field(default_factory=list)
    characters: List[str] = field(default_factory=list)
    series_type: str = ""  # "anime", "live_action", "documentary"

    def detect_type(self) -> str:
        """Detecta tipo da série pelos gêneros."""
        if self.series_type:
            return self.series_type
        genres_lower = [g.lower() for g in self.genres]
        anime_signals = {"animation", "anime", "shounen", "shoujo", "seinen", "josei",
                         "isekai", "mecha", "magical girl", "slice of life"}
        doc_signals = {"documentary", "news", "reality", "talk show"}
        if any(g in anime_signals for g in genres_lower):
            return "anime"
        if any(g in doc_signals for g in genres_lower):
            return "documentary"
        return "live_action"


@dataclass
class TranslationJob:
    """
    Encapsula estado de tradução de um único arquivo/job.
    Garante que traduções simultâneas de séries diferentes
    não compartilhem contexto nem glossário.
    """
    series_metadata: SeriesMetadata = field(default_factory=SeriesMetadata)
    series_glossary: Optional[Dict[str, str]] = None
    translation_context: List[str] = field(default_factory=list)
    profile: TranslationProfile = field(default_factory=TranslationProfile)
    # Telemetria por job
    stats: Dict[str, int] = field(default_factory=lambda: {
        'total_lines': 0,
        'cache_hits': 0,
        'cache_misses': 0,
        'validation_rejections': 0,
        'api_failures': 0,
        'successful_translations': 0,
        'self_consistency_triggered': 0,
        'retry_count': 0,
        'classified_dialogue': 0,
        'classified_sfx': 0,
        'classified_music': 0,
        'classified_tag': 0,
        'classified_untranslatable': 0,
    })
    # Glossário automático: acumula pares (original, tradução) com contagem
    auto_glossary_candidates: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def add_context(self, translated_line: str):
        """Adiciona linha ao contexto deslizante."""
        self.translation_context.append(translated_line)
        max_ctx = self.profile.context_window_size * 2
        if len(self.translation_context) > max_ctx:
            self.translation_context = self.translation_context[-max_ctx:]

    def get_recent_context(self) -> List[str]:
        """Retorna últimas N traduções para contexto."""
        n = self.profile.context_window_size
        return self.translation_context[-n:]

    def track_auto_glossary(self, original: str, translated: str):
        """Rastreia pares para glossário automático."""
        if not self.profile.enable_auto_glossary:
            return
        # Só rastrear termos com capitalização especial (potenciais nomes próprios)
        words_orig = re.findall(r'\b[A-Z][a-zA-Z]+\b', original)
        for word in words_orig:
            word_lower = word.lower()
            if word_lower not in self.auto_glossary_candidates:
                self.auto_glossary_candidates[word_lower] = {}
            # Encontrar como o termo aparece na tradução
            if word in translated:
                count = self.auto_glossary_candidates[word_lower].get(word, 0)
                self.auto_glossary_candidates[word_lower][word] = count + 1

    def get_suggested_glossary(self, min_occurrences: int = 5) -> Dict[str, str]:
        """Retorna termos que apareceram consistentemente."""
        suggested = {}
        for original_lower, translations in self.auto_glossary_candidates.items():
            if not translations:
                continue
            best_trans = max(translations, key=translations.get)
            if translations[best_trans] >= min_occurrences:
                suggested[original_lower] = best_trans
        return suggested


# ────────────────────────────────────────────
# Mapa de nomes de idioma para os prompts
# ────────────────────────────────────────────

_LANG_NAMES: Dict[str, str] = {
    'pt-BR': 'Brazilian Portuguese',
    'pt-PT': 'European Portuguese',
    'pt':    'Portuguese',
    'es':    'Spanish',
    'fr':    'French',
    'de':    'German',
    'it':    'Italian',
    'en':    'English',
    'ja':    'Japanese',
    'ko':    'Korean',
    'zh':    'Chinese',
    'auto':  'English',  # fallback para auto-detect
}


def _lang_name(code: str) -> str:
    """Retorna o nome legível do idioma a partir do código."""
    return _LANG_NAMES.get(code, code)


# ────────────────────────────────────────────
# Prompts base (PT-BR como padrão; parametrizados nos métodos)
# ────────────────────────────────────────────

_SYSTEM_PROMPT_SINGLE = """You are a professional subtitle translator. MANDATORY RULES:

1. Reply with ONLY the translated line
2. NEVER add explanations, comments, 'translation:', etc.
3. Match gender and number agreement correctly
4. Use correct conditional forms
5. Natural, fluent target language
6. Keep formatting [XXX] if present
7. Preserve ellipses (...) and emotional punctuation
8. Use colloquial register when appropriate

TRANSLATE ONLY:"""

_SYSTEM_PROMPT_BATCH = """You are a subtitle translator. Your ONLY task is to receive N numbered lines and return EXACTLY N lines in the SAME format and order.

MANDATORY OUTPUT FORMAT (one line per number, skip none):
1│ translation of line 1
2│ translation of line 2
3│ translation of line 3
... (up to N│)

RULES:
- Return EXACTLY the same number of lines received, in the same order (1, 2, 3, …).
- Use ONLY the format "number│ text" per line. No header, footer, or explanations.
- Natural target language; preserve tone, slang, and dialogue continuity.
- Keep ASS/SRT tags ({\\i1}, {\\an8}, etc.) and formatting; do not translate proper nouns, (*effects*), [notes].
- If a line is only a sound effect or name, repeat it unchanged with the same number."""


def _system_prompt_lean(source_lang: str = 'en', target_lang: str = 'pt-BR') -> str:
    """Prompt enxuto para APIs pagas (GPT, Gemini) — reduz ~60-70% dos tokens."""
    src = _lang_name(source_lang)
    tgt = _lang_name(target_lang)
    return (
        f"Translate the subtitle line from {src} to {tgt}. "
        "Reply with ONLY the translation. "
        "Preserve formatting tags, proper nouns, and punctuation."
    )


class PromptBuilder:
    """
    Monta system prompt + user message para qualquer backend.
    Aplica budget de tokens com prioridade:
      glossário (sempre) > contexto recente > few-shots > metadados descritivos
    """

    def __init__(
        self,
        profile: TranslationProfile,
        glossary_manager=None,
    ):
        self.profile = profile
        self.glossary_manager = glossary_manager

    # ──── Build principal ────

    def build(
        self,
        backend: str,
        text: str,
        job: Optional[TranslationJob] = None,
        fewshot_examples: Optional[List[dict]] = None,
        source_lang: str = 'en',
        target_lang: str = 'pt-BR',
    ) -> Dict[str, Any]:
        """
        Monta prompt adaptado por backend.

        Returns:
            dict com chaves dependendo do backend:
            - Ollama/GPT/Gemini: {"system": str, "user": str, "options": dict}
            - DeepL: {"text": str, "glossary_entries": list}
            - Google: {"text": str}
            - outros: {"system": str, "user": str}
        """
        if backend in ('Ollama', 'GPT', 'Gemini'):
            return self._build_llm_prompt(backend, text, job, fewshot_examples, source_lang, target_lang)
        elif backend == 'DeepL':
            return self._build_deepl_prompt(text, job, source_lang, target_lang)
        elif backend == 'Google':
            return self._build_google_prompt(text, job, source_lang, target_lang)
        else:
            return self._build_fallback_prompt(text, source_lang, target_lang)

    def build_batch(
        self,
        backend: str,
        texts: List[str],
        job: Optional[TranslationJob] = None,
        fewshot_examples: Optional[List[dict]] = None,
        use_batch_prompt: bool = False,
        source_lang: str = 'en',
        target_lang: str = 'pt-BR',
    ) -> Dict[str, Any]:
        """Monta prompt para modo batch (múltiplas linhas numeradas)."""
        if backend != 'Ollama':
            return self._build_llm_prompt(backend, "\n".join(texts), job, fewshot_examples, source_lang, target_lang)

        return self._build_ollama_batch_prompt(texts, job, fewshot_examples, use_batch_prompt, source_lang, target_lang)

    # ──── Prompts LLM (Ollama/GPT/Gemini) ────

    # APIs pagas que devem usar o perfil enxuto de tokens
    _PAID_APIS = frozenset({'GPT', 'Gemini'})

    def _build_llm_prompt(
        self,
        backend: str,
        text: str,
        job: Optional[TranslationJob] = None,
        fewshot_examples: Optional[List[dict]] = None,
        source_lang: str = 'en',
        target_lang: str = 'pt-BR',
    ) -> Dict[str, Any]:
        """Prompt completo para LLMs com contexto + glossário + few-shots.

        Para APIs pagas (GPT, Gemini) usa perfil enxuto: system prompt mínimo,
        glossário limitado a 10 termos da série, sem few-shots e contexto de
        no máximo 2 linhas — reduzindo ~60-70% dos tokens de entrada por chamada.
        """
        is_paid = backend in self._PAID_APIS

        if is_paid:
            return self._build_lean_prompt(backend, text, job, source_lang, target_lang)

        src_name = _lang_name(source_lang)
        tgt_name = _lang_name(target_lang)

        budget = self.profile.max_tokens_budget
        system_parts = [_SYSTEM_PROMPT_SINGLE]
        user_parts = []
        used_tokens = self._estimate_tokens(_SYSTEM_PROMPT_SINGLE)

        # 1. Glossário (SEMPRE — prioridade máxima)
        glossary_section = self._build_glossary_section(job)
        if glossary_section:
            glossary_tokens = self._estimate_tokens(glossary_section)
            if used_tokens + glossary_tokens < budget:
                system_parts.append(glossary_section)
                used_tokens += glossary_tokens

        # 2. Metadados da série
        metadata_section = self._build_metadata_section(job)
        if metadata_section:
            meta_tokens = self._estimate_tokens(metadata_section)
            if used_tokens + meta_tokens < budget:
                system_parts.append(metadata_section)
                used_tokens += meta_tokens

        # 3. Contexto deslizante
        context_section = self._build_context_section(job)
        if context_section:
            ctx_tokens = self._estimate_tokens(context_section)
            if used_tokens + ctx_tokens < budget:
                user_parts.append(context_section)
                used_tokens += ctx_tokens

        # 4. Few-shots (se habilitado, couber no budget e par de idiomas for EN→PT-BR)
        if fewshot_examples and self.profile.enable_fewshot and source_lang in ('en', 'auto') and target_lang == 'pt-BR':
            fewshot_section = self._build_fewshot_section(fewshot_examples)
            fs_tokens = self._estimate_tokens(fewshot_section)
            if used_tokens + fs_tokens < budget:
                user_parts.insert(0, fewshot_section)
                used_tokens += fs_tokens

        # 5. Texto a traduzir (sempre por último)
        user_parts.append(
            f"TRANSLATE the line below from {src_name} to {tgt_name}.\n"
            f"IMPORTANT: You MUST translate it. Do NOT return the original {src_name} text.\n"
            f"RESPOND WITH ONLY THE TRANSLATION in {tgt_name}. NO explanations. NO notes.\n\n"
            f"{src_name}: {text}\n{tgt_name}:"
        )

        system = "\n\n".join(system_parts)
        user = "\n\n".join(user_parts)

        result = {"system": system, "user": user}

        if backend == 'Ollama':
            result["options"] = self.profile.get_ollama_options(len(text))
            result["options"]["stop"] = [
                "\n", "\\n", "Nota:", "Note:", "explain", "explicar",
                "English:", "Inglês:", "Previous context"
            ]

        return result

    def _build_lean_prompt(
        self,
        backend: str,
        text: str,
        job: Optional[TranslationJob] = None,
        source_lang: str = 'en',
        target_lang: str = 'pt-BR',
    ) -> Dict[str, Any]:
        """Prompt enxuto para APIs pagas (GPT, Gemini).

        Estratégia de economia:
        - System prompt mínimo (~15 tokens vs ~200 do prompt completo)
        - Glossário limitado a 10 termos da série (sem os 50 globais)
        - Sem few-shot examples
        - Contexto máximo de 2 linhas anteriores
        Resultado: ~60-70% menos tokens de entrada por chamada.
        """
        src_name = _lang_name(source_lang)
        tgt_name = _lang_name(target_lang)
        system_parts = [_system_prompt_lean(source_lang, target_lang)]

        # Glossário: apenas termos da série, máximo 10
        if job and job.series_glossary and self.glossary_manager:
            series_terms = list(job.series_glossary.items())[:10]
            if series_terms:
                terms_str = "\n".join(f"  {k} → {v}" for k, v in series_terms)
                system_parts.append(f"Keep these terms untranslated:\n{terms_str}")

        system = "\n\n".join(system_parts)

        # Contexto: máximo 2 linhas anteriores
        user_parts = []
        if job and self.profile.enable_contextual_prompt:
            recent = job.translation_context[-2:] if job.translation_context else []
            if recent:
                ctx = " / ".join(recent)
                user_parts.append(f"[Previous: {ctx}]")

        user_parts.append(f"{src_name}: {text}\n{tgt_name}:")
        user = "\n".join(user_parts)

        return {"system": system, "user": user}

    def _build_ollama_batch_prompt(
        self,
        texts: List[str],
        job: Optional[TranslationJob] = None,
        fewshot_examples: Optional[List[dict]] = None,
        use_batch_prompt: bool = False,
        source_lang: str = 'en',
        target_lang: str = 'pt-BR',
    ) -> Dict[str, Any]:
        """Prompt batch para Ollama."""
        N = len(texts)
        tgt_name = _lang_name(target_lang)

        # Construir batch numerado
        numbered_batch = "\n".join(f"{i+1}│ {t}" for i, t in enumerate(texts))

        # Glossário
        glossary_section = self._build_glossary_section(job)

        # Contexto
        context_section = ""
        if job and job.translation_context:
            recent = job.get_recent_context()
            if recent:
                context_section = "Previous context (use for consistency, do NOT translate):\n"
                for i, ctx in enumerate(recent[-3:], 1):
                    context_section += f"  [{i}] {ctx}\n"
                context_section += "\n"

        if use_batch_prompt:
            system_content = _SYSTEM_PROMPT_BATCH
            if glossary_section:
                system_content += f"\n\n{glossary_section}"

            example_lines = "\n".join([f"{i}│ ..." for i in range(1, min(N, 4) + 1)])
            if N > 4:
                example_lines += f"\n...\n{N}│ ..."

            user_message = (
                f"{context_section}"
                f"Translate the {N} lines below to {tgt_name}. "
                f"Reply with ONLY {N} lines in the format:\n"
                f"{example_lines}\n\n"
                f"INPUT ({N} lines):\n{numbered_batch}"
            )
        else:
            system_content = _SYSTEM_PROMPT_SINGLE
            if glossary_section:
                system_content += f"\n\n{glossary_section}"

            user_message = (
                f"{context_section}"
                f"Translate ONLY the numbered lines below to {tgt_name}, "
                f"keeping tone, slang, and dialogue continuity.\n"
                f"Reply with ONLY the translated lines in the same numbered format (1│ translation):\n\n"
                f"{numbered_batch}\n"
                f"Rules:\n"
                f"- ONLY the numbered lines (format: 1│ translated text)\n"
                f"- Preserve ASS/SSA tags like {{\\i1}}, {{\\an8}}, etc.\n"
                f"- Do not translate proper nouns, sound effects (*sigh*), onomatopoeia\n"
                f"- Use pronouns and tone consistent with the previous context"
            )

        return {
            "system": system_content,
            "user": user_message,
            "options": self.profile.get_batch_ollama_options(len(numbered_batch)),
        }

    # ──── Prompts para APIs não-LLM ────

    def _build_deepl_prompt(
        self,
        text: str,
        job: Optional[TranslationJob] = None,
        source_lang: str = 'en',
        target_lang: str = 'pt-BR',
    ) -> Dict[str, Any]:
        """Para DeepL: usa glossary entries nativos + contexto como prefixo."""
        glossary_entries = []
        if job and job.series_glossary and self.glossary_manager:
            combined = {**self.glossary_manager.global_glossary}
            combined.update(job.series_glossary)
            glossary_entries = [
                {"source": k, "target": v}
                for k, v in list(combined.items())[:50]  # DeepL limita glossário
            ]

        enriched_text = text
        if job and self.profile.enable_contextual_prompt:
            recent = job.get_recent_context()
            if recent:
                ctx = " // ".join(recent[-2:])
                enriched_text = f"[Context: {ctx}] {text}"

        return {
            "text": enriched_text,
            "glossary_entries": glossary_entries,
        }

    def _build_google_prompt(
        self,
        text: str,
        job: Optional[TranslationJob] = None,
        source_lang: str = 'en',
        target_lang: str = 'pt-BR',
    ) -> Dict[str, Any]:
        """Para Google Translate: glossário inline (limitado)."""
        enriched_text = text
        if job and job.series_glossary:
            important_terms = list(job.series_glossary.items())[:10]
            if important_terms:
                terms_str = ", ".join(f"{k}={v}" for k, v in important_terms)
                enriched_text = f"[Keep: {terms_str}] {text}"

        return {"text": enriched_text}

    def _build_fallback_prompt(
        self,
        text: str,
        source_lang: str = 'en',
        target_lang: str = 'pt-BR',
    ) -> Dict[str, Any]:
        """Fallback sem contexto nem few-shots."""
        tgt_name = _lang_name(target_lang)
        return {
            "system": _SYSTEM_PROMPT_SINGLE,
            "user": f"Translate to {tgt_name}:\n{text}",
        }

    # ──── Seções auxiliares ────

    def _build_glossary_section(self, job: Optional[TranslationJob] = None) -> str:
        """Seção de glossário obrigatório para o prompt."""
        if not self.glossary_manager:
            return ""
        if not job or not job.series_glossary:
            return ""
        
        # Combinar glossário global + série
        combined = {**self.glossary_manager.global_glossary}
        combined.update(job.series_glossary)

        # Limitar para caber no budget (priorizar série)
        items = []
        # Primeiro termos da série
        for k, v in sorted(job.series_glossary.items()):
            items.append(f"  {k} → {v}")
        # Depois globais (até limite)
        max_global = 50
        count = 0
        for k, v in sorted(self.glossary_manager.global_glossary.items()):
            if k not in job.series_glossary:
                items.append(f"  {k} → {v}")
                count += 1
                if count >= max_global:
                    break

        if not items:
            return ""

        return (
            "GLOSSÁRIO OBRIGATÓRIO — use exatamente estes termos:\n"
            + "\n".join(items)
            + "\n\nREGRA CRÍTICA: Mantenha estes termos SEM TRADUZIR em suas respostas."
        )

    def _build_metadata_section(self, job: Optional[TranslationJob] = None) -> str:
        """Metadados da série para enriquecer contexto."""
        if not job or not job.series_metadata.title:
            return ""
        
        meta = job.series_metadata
        parts = [f"Série: {meta.title}"]
        if meta.genres:
            parts.append(f"Gêneros: {', '.join(meta.genres)}")
        if meta.characters:
            parts.append(f"Personagens: {', '.join(meta.characters[:10])}")
        series_type = meta.detect_type()
        if series_type:
            parts.append(f"Tipo: {series_type}")
        
        return "\n".join(parts)

    def _build_context_section(self, job: Optional[TranslationJob] = None) -> str:
        """Contexto deslizante das traduções recentes."""
        if not job or not self.profile.enable_contextual_prompt:
            return ""
        
        recent = job.get_recent_context()
        if not recent:
            return ""

        lines = ["Contexto anterior (leia para consistência, NÃO traduza):"]
        for i, ctx in enumerate(recent, 1):
            lines.append(f"  [anterior -{len(recent) - i + 1}]: {ctx}")
        
        return "\n".join(lines)

    def _build_fewshot_section(self, examples: List[dict]) -> str:
        """Few-shot examples formatados."""
        if not examples:
            return ""
        
        lines = ["Exemplos de tradução (siga o mesmo estilo):"]
        for ex in examples:
            lines.append(f"  EN: {ex.get('en', '')}")
            lines.append(f"  PT: {ex.get('pt', '')}")
            lines.append("")
        
        return "\n".join(lines)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimativa grosseira de tokens (~4 chars por token)."""
        return len(text) // 4
