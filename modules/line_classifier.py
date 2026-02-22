"""
LineClassifier - Classifica linhas de legenda antes da tradução.
Custo zero: baseado em regex + heurística, sem dependência externa.
"""
import re
from enum import Enum
from typing import Tuple


class LineType(Enum):
    DIALOGUE = "dialogue"
    SOUND_EFFECT = "sound_effect"
    MUSIC_LYRICS = "music_lyrics"
    TECHNICAL_TAG = "technical_tag"
    UNTRANSLATABLE = "untranslatable"


# Onomatopeias comuns em anime/séries que devem ser mantidas
_ONOMATOPOEIA = {
    "bang", "boom", "pow", "crash", "splash", "thud", "whoosh", "buzz",
    "hiss", "click", "clack", "snap", "crack", "pop", "thump", "slam",
    "screech", "rumble", "clang", "swoosh", "whack", "zap", "beep",
    "boing", "ding", "dong", "wham", "zoom", "vroom",
}

# Termos japoneses comuns que não devem ser traduzidos
_JAPANESE_KEEP = {
    "bankai", "sharingan", "rasengan", "kamehameha", "jutsu", "chakra",
    "senpai", "sensei", "sama", "kun", "chan", "san", "dono",
    "nani", "baka", "sugoi", "kawaii", "yatta", "ganbatte",
    "itadakimasu", "gochisousama", "tadaima", "okaeri",
    "ohayo", "konnichiwa", "konbanwa", "sayonara", "matte",
}

# Regex patterns compilados para performance
_RE_MUSIC = re.compile(r'^\s*[♪♫🎵🎶]+.*[♪♫🎵🎶]+\s*$', re.DOTALL)
_RE_MUSIC_TAG = re.compile(r'^\s*[♪♫🎵🎶]', re.MULTILINE)
_RE_SOUND_BRACKET = re.compile(r'^\s*[\[\(]([^\]\)]+)[\]\)]\s*$')
_RE_SOUND_ASTERISK = re.compile(r'^\s*\*([^*]+)\*\s*$')
_RE_ASS_FULL_TAG = re.compile(r'^\s*\{[^}]+\}\s*$')
_RE_ASS_POS_ONLY = re.compile(r'^\s*\{\\(?:pos|move|org|clip|fad|an\d|r)\([^)]*\)\}\s*$')
_RE_TECHNICAL = re.compile(
    r'^\s*\{\\(?:an\d|pos|move|org|clip|fad|fade|blur|bord|shad|fs|fn|fe|'
    r'fr[xyz]?|fsc[xy]|fsp|1c|2c|3c|4c|alpha|i?clip|p\d|t\()'
)
_RE_ONLY_PUNCTUATION = re.compile(r'^[\s\W]+$')
_RE_SOUND_WORDS = re.compile(
    r'^\s*[\[\(]?\s*\b('
    r'sighs?|gasps?|groans?|screams?|laughs?|coughs?|sobs?|sniffs?|'
    r'chuckles?|giggles?|whispers?|shouts?|yells?|cries?|moans?|'
    r'grunts?|snores?|growls?|hums?|whistles?|claps?|knocks?|'
    r'footsteps|gunshots?|explosions?|thunder|wind|rain|door|phone|'
    r'music playing|indistinct chatter|crowd cheering|alarm|siren|'
    r'breathing|panting|stammering|stuttering|'
    r'ringing|beeping|buzzing|ticking|clicking|creaking|'
    r'applause|laughter|silence|static|'
    r'speaking [a-z]+|talking|singing|crying|sobbing|wailing|'
    r'inhales?|exhales?'
    r')\s*[\]\)]?\s*$',
    re.IGNORECASE
)


class LineClassifier:
    """
    Classifica cada linha de legenda para determinar como processá-la.
    
    - DIALOGUE: traduzir normalmente via LLM
    - SOUND_EFFECT: traduzir via regras simples (sem LLM)
    - MUSIC_LYRICS: manter original ou traduzir com prompt dedicado
    - TECHNICAL_TAG: preservar intacto, não enviar ao modelo
    - UNTRANSLATABLE: manter original (onomatopeias, termos japoneses)
    """

    # Traduções de efeitos sonoros comuns (EN → PT-BR)
    SOUND_EFFECT_TRANSLATIONS = {
        "sighs": "suspira", "sigh": "suspiro",
        "gasps": "ofega", "gasp": "ofego",
        "groans": "geme", "groan": "gemido",
        "screams": "grita", "scream": "grito",
        "laughs": "ri", "laugh": "risada",
        "laughing": "rindo", "laughter": "risadas",
        "coughs": "tosse", "cough": "tosse",
        "sobs": "soluça", "sob": "soluço",
        "sobbing": "soluçando",
        "sniffs": "funga", "sniff": "fungada",
        "chuckles": "dá risada", "chuckle": "risadinha",
        "giggles": "dá risadinha", "giggle": "risadinha",
        "whispers": "sussurra", "whisper": "sussurro",
        "whispering": "sussurrando",
        "shouts": "grita", "shout": "grito",
        "shouting": "gritando",
        "yells": "berra", "yell": "berro",
        "yelling": "berrando",
        "cries": "chora", "cry": "choro",
        "crying": "chorando",
        "moans": "geme", "moan": "gemido",
        "grunts": "rosna", "grunt": "rosnado",
        "growls": "rosna", "growl": "rosnado",
        "hums": "cantarola", "hum": "cantarolar",
        "humming": "cantarolando",
        "whistles": "assobia", "whistle": "assobio",
        "claps": "aplaude", "clap": "aplauso",
        "knocks": "bate", "knock": "batida",
        "knocking": "batendo na porta",
        "footsteps": "passos",
        "gunshot": "tiro", "gunshots": "tiros",
        "explosion": "explosão", "explosions": "explosões",
        "thunder": "trovão",
        "wind": "vento",
        "rain": "chuva",
        "door": "porta",
        "phone": "telefone",
        "music playing": "música tocando",
        "indistinct chatter": "conversa indistinta",
        "crowd cheering": "multidão comemorando",
        "alarm": "alarme",
        "siren": "sirene",
        "breathing": "respirando",
        "panting": "ofegando",
        "stammering": "gaguejando",
        "stuttering": "gaguejando",
        "ringing": "tocando",
        "beeping": "bipando",
        "buzzing": "zumbindo",
        "ticking": "tiquetaqueando",
        "clicking": "clicando",
        "creaking": "rangendo",
        "applause": "aplausos",
        "silence": "silêncio",
        "static": "estática",
        "singing": "cantando",
        "talking": "falando",
        "wailing": "lamentando",
        "inhales": "inspira", "inhale": "inspiração",
        "exhales": "expira", "exhale": "expiração",
        "snoring": "roncando", "snores": "ronca",
        "screaming": "gritando",
        "gasping": "ofegando",
        "groaning": "gemendo",
        "coughing": "tossindo",
        "sniffing": "fungando",
    }

    def classify(self, text: str) -> Tuple[LineType, str]:
        """
        Classifica uma linha e retorna (tipo, texto_processado).
        
        Para SOUND_EFFECT, texto_processado já é a tradução.
        Para TECHNICAL_TAG e UNTRANSLATABLE, texto_processado é o original.
        Para DIALOGUE e MUSIC_LYRICS, texto_processado é o texto limpo.
        """
        if not text or not text.strip():
            return LineType.UNTRANSLATABLE, text or ""

        stripped = text.strip()

        # 1. Tag técnica pura (só tags ASS sem texto)
        if _RE_ASS_FULL_TAG.match(stripped):
            return LineType.TECHNICAL_TAG, text

        # 2. Só pontuação/símbolos
        if _RE_ONLY_PUNCTUATION.match(stripped):
            return LineType.UNTRANSLATABLE, text

        # 3. Música (♪ ... ♪)
        if _RE_MUSIC.match(stripped) or (stripped.startswith('♪') and stripped.endswith('♪')):
            return LineType.MUSIC_LYRICS, stripped

        # 4. Efeito sonoro entre colchetes/parênteses: [door creaking], (sighs)
        bracket_match = _RE_SOUND_BRACKET.match(stripped)
        if bracket_match:
            inner = bracket_match.group(1).strip().lower()
            translated = self._translate_sound_effect(inner)
            if translated != inner:
                # Preservar delimitadores originais
                open_char = stripped[0]
                close_char = stripped[-1]
                return LineType.SOUND_EFFECT, f"{open_char}{translated}{close_char}"
            # Se o conteúdo entre colchetes parece efeito sonoro
            if _RE_SOUND_WORDS.match(stripped):
                translated_sfx = self._translate_sound_effect(inner)
                open_char = stripped[0]
                close_char = stripped[-1]
                return LineType.SOUND_EFFECT, f"{open_char}{translated_sfx}{close_char}"

        # 5. Efeito sonoro entre asteriscos: *sighs*
        asterisk_match = _RE_SOUND_ASTERISK.match(stripped)
        if asterisk_match:
            inner = asterisk_match.group(1).strip().lower()
            translated = self._translate_sound_effect(inner)
            return LineType.SOUND_EFFECT, f"*{translated}*"

        # 6. Linha que é só uma palavra de efeito sonoro (sem delimitadores)
        if _RE_SOUND_WORDS.match(stripped):
            inner = stripped.strip('[]() ').lower()
            translated = self._translate_sound_effect(inner)
            return LineType.SOUND_EFFECT, translated

        # 7. Onomatopeia pura
        if stripped.lower().rstrip('!.').strip() in _ONOMATOPOEIA:
            return LineType.UNTRANSLATABLE, text

        # 8. Termo japonês preservado
        if stripped.lower().rstrip('!.').strip() in _JAPANESE_KEEP:
            return LineType.UNTRANSLATABLE, text

        # 9. Texto muito curto sem conteúdo traduzível
        alpha_count = sum(1 for c in stripped if c.isalpha())
        if alpha_count < 2:
            return LineType.UNTRANSLATABLE, text

        # 10. Default: diálogo
        return LineType.DIALOGUE, stripped

    def _translate_sound_effect(self, effect_text: str) -> str:
        """Traduz efeito sonoro usando dicionário."""
        effect_lower = effect_text.lower().strip()

        # Busca direta
        if effect_lower in self.SOUND_EFFECT_TRANSLATIONS:
            return self.SOUND_EFFECT_TRANSLATIONS[effect_lower]

        # Busca parcial: "door creaking" → "porta rangendo"
        for en, pt in self.SOUND_EFFECT_TRANSLATIONS.items():
            if en in effect_lower:
                return effect_lower.replace(en, pt)

        return effect_text

    def classify_batch(self, texts: list) -> list:
        """Classifica múltiplas linhas de uma vez."""
        return [self.classify(t) for t in texts]
