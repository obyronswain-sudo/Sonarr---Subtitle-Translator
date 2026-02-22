"""
Banco de exemplos few-shot por gênero para tradução de legendas.
Cada gênero tem 3-4 exemplos que demonstram tom, estilo e desafios comuns.
"""
from typing import List, Dict, Optional


# ──── Exemplos por gênero ────

FEWSHOT_ANIME = [
    {
        "en": "If my lifespan was predetermined, I wonder how I'd handle that?",
        "pt": "Se minha vida fosse predeterminada, me pergunto como eu lidaria com isso?"
    },
    {
        "en": "Don't underestimate the power of a Saiyan!",
        "pt": "Não subestime o poder de um Saiyan!"
    },
    {
        "en": "Senpai, you really saved me back there. Arigato!",
        "pt": "Senpai, você realmente me salvou lá atrás. Arigato!"
    },
    {
        "en": "I'll never forgive you for what you did to my nakama!",
        "pt": "Eu nunca vou te perdoar pelo que fez com meus nakama!"
    },
]

FEWSHOT_LIVE_ACTION = [
    {
        "en": "Look, I know it's none of my business, but you gotta stop doing this to yourself.",
        "pt": "Olha, eu sei que não é da minha conta, mas você precisa parar de fazer isso consigo mesmo."
    },
    {
        "en": "Are you kidding me right now? This is the worst timing ever!",
        "pt": "Tá de brincadeira comigo? Esse é o pior momento possível!"
    },
    {
        "en": "I've been thinking... maybe we should take a break.",
        "pt": "Eu tava pensando... talvez a gente devesse dar um tempo."
    },
    {
        "en": "Dude, you're not gonna believe what just happened.",
        "pt": "Cara, você não vai acreditar no que acabou de acontecer."
    },
]

FEWSHOT_DOCUMENTARY = [
    {
        "en": "The migration patterns of these species have been extensively studied over the past decade.",
        "pt": "Os padrões migratórios dessas espécies foram extensivamente estudados na última década."
    },
    {
        "en": "Scientists believe that climate change could drastically alter the ecosystem within the next 50 years.",
        "pt": "Cientistas acreditam que as mudanças climáticas podem alterar drasticamente o ecossistema nos próximos 50 anos."
    },
    {
        "en": "This remarkable discovery challenges everything we thought we knew about human evolution.",
        "pt": "Essa descoberta notável desafia tudo que pensávamos saber sobre a evolução humana."
    },
]

FEWSHOT_NEUTRAL = [
    {
        "en": "If my lifespan was predetermined",
        "pt": "Se minha vida fosse predeterminada"
    },
    {
        "en": "I wonder how I'd handle that?",
        "pt": "Me pergunto como eu lidaria com isso?"
    },
    {
        "en": "Don't......",
        "pt": "Não..."
    },
    {
        "en": "What the hell are you talking about?",
        "pt": "Que droga você tá falando?"
    },
]

# Mapeamento de gêneros para conjuntos de exemplos
_GENRE_MAP = {
    "anime": FEWSHOT_ANIME,
    "animation": FEWSHOT_ANIME,
    "shounen": FEWSHOT_ANIME,
    "shoujo": FEWSHOT_ANIME,
    "seinen": FEWSHOT_ANIME,
    "josei": FEWSHOT_ANIME,
    "isekai": FEWSHOT_ANIME,
    "mecha": FEWSHOT_ANIME,
    "magical girl": FEWSHOT_ANIME,
    "slice of life": FEWSHOT_ANIME,

    "live_action": FEWSHOT_LIVE_ACTION,
    "drama": FEWSHOT_LIVE_ACTION,
    "comedy": FEWSHOT_LIVE_ACTION,
    "action": FEWSHOT_LIVE_ACTION,
    "thriller": FEWSHOT_LIVE_ACTION,
    "horror": FEWSHOT_LIVE_ACTION,
    "romance": FEWSHOT_LIVE_ACTION,
    "crime": FEWSHOT_LIVE_ACTION,
    "mystery": FEWSHOT_LIVE_ACTION,
    "sci-fi": FEWSHOT_LIVE_ACTION,
    "fantasy": FEWSHOT_LIVE_ACTION,
    "adventure": FEWSHOT_LIVE_ACTION,
    "western": FEWSHOT_LIVE_ACTION,
    "war": FEWSHOT_LIVE_ACTION,

    "documentary": FEWSHOT_DOCUMENTARY,
    "news": FEWSHOT_DOCUMENTARY,
    "reality": FEWSHOT_DOCUMENTARY,
    "talk show": FEWSHOT_DOCUMENTARY,
    "educational": FEWSHOT_DOCUMENTARY,
    "history": FEWSHOT_DOCUMENTARY,
    "science": FEWSHOT_DOCUMENTARY,
    "nature": FEWSHOT_DOCUMENTARY,
    "biography": FEWSHOT_DOCUMENTARY,
}


def get_fewshot_examples(
    series_type: str = "",
    genres: Optional[List[str]] = None,
    max_examples: int = 4,
) -> List[Dict[str, str]]:
    """
    Seleciona exemplos few-shot com base no tipo/gênero da série.
    
    Args:
        series_type: "anime", "live_action", "documentary" ou ""
        genres: Lista de gêneros da série (do Sonarr/AniList)
        max_examples: Número máximo de exemplos a retornar
        
    Returns:
        Lista de dicts {"en": ..., "pt": ...}
    """
    # Primeiro tenta por series_type
    if series_type:
        examples = _GENRE_MAP.get(series_type.lower())
        if examples:
            return examples[:max_examples]

    # Depois tenta por gêneros individuais
    if genres:
        for genre in genres:
            examples = _GENRE_MAP.get(genre.lower())
            if examples:
                return examples[:max_examples]

    # Fallback: exemplos neutros
    return FEWSHOT_NEUTRAL[:max_examples]
