import re
import time
import json
import os
import threading
import requests
from pathlib import Path
from typing import Dict, Optional, List, Callable
from datetime import datetime, timedelta

# Termos comuns a ignorar no auto-merge (anti-envenenamento)
_STOPWORDS_GLOSSARY = frozenset({
    'the', 'and', 'for', 'you', 'are', 'not', 'but', 'his', 'her', 'has', 'had',
    'was', 'all', 'can', 'out', 'did', 'get', 'him', 'say', 'she', 'they', 'this',
    'with', 'that', 'from', 'have', 'will', 'one', 'yes', 'no', 'ok', 'oh', 'ah',
})


class GlossaryManager:
    """
    Gerenciador de glossário para tradução de legendas de anime.
    Combina um glossário global fixo com termos específicos de cada série.
    """

    # Glossário global de termos de anime comuns (EN → PT-BR)
    GLOBAL_GLOSSARY = {
        # Saudações e honoríficos
        "senpai": "senpai",
        "sempai": "senpai",
        "sempayan": "senpai",
        "sensei": "sensei",
        "sensei": "sensei",
        "dono": "dono",
        "onii-san": "onii-san",
        "onii-chan": "onii-chan",
        "onee-san": "onee-san",
        "onee-chan": "onee-chan",
        "nii-san": "nii-san",
        "nii-chan": "nii-chan",
        "ojou-sama": "ojou-sama",
        "ojousama": "ojou-sama",
        "sama": "sama",
        "san": "san",
        "kun": "kun",
        "chan": "chan",
        "domo": "domo",
        "arigato": "arigato",
        "arigatou": "arigato",
        "gozaimasu": "gozaimasu",
        "dou itashimashite": "de nada",
        "sumimasen": "sumimasen",
        "gomennasai": "gomennasai",
        "gomen": "gomen",

        # Características/Atributos
        "baka": "idiota",
        "hentai": "pervertido",
        "ecchi": "ecchi",
        "kawaii": "fofinho",
        "kawaiii": "fofinho",
        "sugoi": "incrível",
        "yabai": "perigoso",
        "yabakute": "perigoso",
        "kowai": "assustador",
        "kyodai": "incrível",
        "yamete": "pare",
        "dame": "não faça",
        "iya": "não",
        "iya desu": "não quero",
        "nani": "o quê",
        "nande": "por quê",
        "doshite": "por que",

        # Conceitos de anime/manga
        "tsundere": "tsundere",
        "yandere": "yandere",
        "kuudere": "kuudere",
        "dandere": "dandere",
        "deredere": "deredere",
        "himegimi": "princesa",
        "otaku": "otaku",
        "fujoshi": "fujoshi",
        "yaoi": "yaoi",
        "shoujo": "shoujo",
        "shounen": "shounen",
        "seinen": "seinen",
        "josei": "josei",
        "nakama": "companheiro",
        "senshi": "guerreiro",
        "kamikaze": "kamikaze",
        "bushido": "bushido",
        "dojo": "dojo",
        "dohyo": "dohyo",
        "harem": "harém",
        "harem": "harém",
        "isekai": "isekai",
        "chibi": "chibi",
        "neko": "gato",
        "nyan": "miau",
        "kitsune": "raposa",
        "youkai": "youkai",
        "kami": "kami",
        "shinigami": "shinigami",
        "reaper": "shinigami",

        # Alimentos e bebidas
        "sake": "sake",
        "sushi": "sushi",
        "ramen": "ramen",
        "dango": "dango",
        "mochi": "mochi",
        "tofu": "tofu",
        "tempura": "tempura",
        "karaoke": "karaokê",
        "bento": "bento",
        "baka-bento": "marmita de idiota",

        # Ações e expressões
        "kawii": "fofo",
        "sugee": "legal",
        "yabai": "perigoso",
        "arigatai": "agradável",
        "kowai": "assustador",
        "attakai": "quente",
        "amai": "doce",
        "karai": "picante",
        "seppuku": "suicídio ritualístico",
        "harakiri": "suicídio ritualístico",
        "kamikaze": "ataque de sacrifício",
        "bukake": "banho em grupo",

        # Técnicas e habilidades
        "jutsu": "jutsu",
        "kenjutsu": "kenjutsu",
        "taijutsu": "taijutsu",
        "ninjutsu": "ninjutsu",
        "genjutsu": "genjutsu",
        "chakra": "chakra",
        "ki": "ki",
        "chi": "ki",
        "hadouken": "hadouken",
        "kamehameha": "kamehameha",
        "rasengan": "rasengan",
        "chidori": "chidori",
        "zanpakuto": "zanpakuto",
        "bankai": "bankai",
        "shikai": "shikai",
        "tatakae": "lute",

        # Lugares
        "dojo": "dojo",
        "dohyo": "dohyo",
        "onsen": "onsen",
        "hot spring": "onsen",
        "shrine": "santuário",
        "temple": "templo",
        "sake bar": "bar de sake",
        "izakaya": "izakaia",
        "convenience store": "konbini",
        "konbini": "konbini",

        # Família
        "otousan": "papai",
        "otou-san": "papai",
        "okaasan": "mamãe",
        "okaa-san": "mamãe",
        "oyomesan": "esposa",
        "shujin": "marido",
        "kodomo": "criança",
        "musume": "filha",
        "musuko": "filho",
        "ani": "irmão mais velho",
        "ane": "irmã mais velha",
        "ototo": "irmão mais novo",
        "imoto": "irmã mais nova",
        "sobo": "avó",
        "ojii-san": "avô",

        # Eventos
        "festival": "festival",
        "matsuri": "matsuri",
        "hanami": "hanami",
        "sakura": "cerejeira",
        "christmas": "natal",
        "new year": "ano novo",
        "valentine": "dia dos namorados",
        "white day": "dia branco",

        # Genéricos
        "sugoi": "uau",
        "nani": "o quê",
        "hai": "sim",
        "iie": "não",
        "okaa": "ok",
        "yoshi": "ok",
        "tasukete": "me ajude",
        "tasuke": "salve",
        "bakka": "idiota",
        
        # Emoções e reações
        "ureshii": "feliz",
        "tanoshii": "divertido",
        "kanashii": "triste",
        "sabishii": "solitário",
        "kowai": "com medo",
        "tsumaranai": "entediante",
        "omoshiroi": "interessante",
        "mendokusai": "problemático",
        "fuzakeruna": "não brinque comigo",
        "uso": "mentira",
        "maji": "sério",
        "majide": "sério mesmo",
        "yatta": "consegui",
        "yokatta": "que bom",
        "yabai": "caramba",
        
        # Tempo e frequência
        "ima": "agora",
        "ashita": "amanhã",
        "kyou": "hoje",
        "kinou": "ontem",
        "asa": "manhã",
        "yoru": "noite",
        "yuugata": "entardecer",
        "ban": "noite",
        "itsumo": "sempre",
        "tokidoki": "às vezes",
        "zenzen": "de jeito nenhum",
        "mada": "ainda",
        "mou": "já",
        
        # Quantidade e intensidade
        "takusan": "muito",
        "ippai": "cheio",
        "sukoshi": "um pouco",
        "chotto": "um pouco",
        "motto": "mais",
        "zenbu": "tudo",
        "subete": "tudo",
        "nani mo": "nada",
        "amari": "muito",
        "totemo": "muito",
        "meccha": "super",
        "hontou": "verdade",
        "honto": "verdade",
        "uso": "mentira",
        
        # Verbos comuns (forma masu)
        "ikimasu": "vou",
        "kimasu": "venho",
        "mimasu": "vejo",
        "kikimasu": "ouço",
        "nomimasu": "bebo",
        "tabemasu": "como",
        "hanashimasu": "falo",
        "kaimasu": "compro",
        "urimasu": "vendo",
        "machimasu": "espero",
        "wakarimasu": "entendo",
        "shirimasu": "sei",
        "oshiemasu": "ensino",
        "naraimasu": "aprendo",
        
        # Verbos informais (forma te/ta)
        "itte": "indo",
        "kite": "vindo",
        "mite": "olhando",
        "kiite": "ouvindo",
        "nonde": "bebendo",
        "tabete": "comendo",
        "hanashite": "falando",
        "katte": "comprando",
        "matte": "esperando",
        "shitte": "sabendo",
        "yonde": "lendo",
        "kaite": "escrevendo",
        "ita": "estava",
        "itta": "foi",
        "kita": "veio",
        
        # Adjetivos comuns
        "atsui": "quente",
        "samui": "frio",
        "atatakai": "morno",
        "tsumetai": "gelado",
        "oishii": "delicioso",
        "mazui": "ruim de comer",
        "ii": "bom",
        "warui": "ruim",
        "hayai": "rápido",
        "osoi": "lento",
        "takai": "alto/caro",
        "yasui": "barato",
        "tooi": "longe",
        "chikai": "perto",
        "ookii": "grande",
        "chiisai": "pequeno",
        "nagai": "longo",
        "mijikai": "curto",
        "muzukashii": "difícil",
        "yasashii": "fácil/gentil",
        "tsuyoi": "forte",
        "yowai": "fraco",
        
        # Títulos e posições
        "buchou": "capitão",
        "taichou": "capitão",
        "kaicho": "presidente",
        "shachou": "presidente da empresa",
        "heika": "majestade",
        "denka": "alteza",
        "ouji": "príncipe",
        "oujo": "princesa",
        "hime": "princesa",
        "ou": "rei",
        "joou": "rainha",
        "shogun": "shogun",
        "daimyo": "daimyo",
        "samurai": "samurai",
        "ninja": "ninja",
        "ronin": "ronin",
        "senpai": "senpai",
        "kouhai": "kouhai",
        
        # Armas e equipamentos
        "katana": "katana",
        "wakizashi": "wakizashi",
        "tanto": "tanto",
        "naginata": "naginata",
        "yari": "lança",
        "yumi": "arco",
        "ya": "flecha",
        "shuriken": "shuriken",
        "kunai": "kunai",
        "bokken": "bokken",
        "shinai": "shinai",
        "gi": "uniforme",
        "hakama": "hakama",
        "kimono": "quimono",
        "yukata": "yukata",
        "geta": "geta",
        "zori": "zori",
        "tabi": "tabi",
        
        # Conceitos de batalha
        "tatakai": "batalha",
        "sensou": "guerra",
        "shiai": "competição",
        "shoubu": "duelo",
        "kettou": "duelo",
        "kessen": "batalha decisiva",
        "satsujin": "assassinato",
        "ansatsu": "assassinato",
        "gyakusatsu": "massacre",
        "hissatsu": "golpe mortal",
        "ichigeki": "um golpe",
        "ougi": "técnica secreta",
        "hiougi": "técnica suprema",
        
        # Poder e magia
        "mana": "mana",
        "mahou": "magia",
        "jumon": "feitiço",
        "noroi": "maldição",
        "fukkatsu": "ressurreição",
        "kaifuku": "cura",
        "chiyu": "cura",
        "baria": "barreira",
        "kekkai": "barreira",
        "sosei": "ressurreição",
        "shoukan": "invocação",
        "tensei": "reencarnação",
        
        # Mitologia e criaturas
        "ryuu": "dragão",
        "doragon": "dragão",
        "oni": "oni",
        "akuma": "demônio",
        "tenshi": "anjo",
        "yokai": "yokai",
        "yurei": "fantasma",
        "obake": "fantasma",
        "vampire": "vampiro",
        "kyuuketsuki": "vampiro",
        "ookami": "lobo",
        "fenrir": "fenrir",
        "phoenix": "fênix",
        "houou": "fênix",
        
        # Escola e acadêmico
        "gakkou": "escola",
        "kyoushitsu": "sala de aula",
        "seito": "estudante",
        "sensei": "professor",
        "kyoushi": "professor",
        "jugyou": "aula",
        "shiken": "prova",
        "tesuto": "teste",
        "숙제": "숙제",
        "shukudai": "dever de casa",
        "benkyou": "estudo",
        "taiiku": "educação física",
        "bukatsu": "clube",
        "kurabu": "clube",
        
        # Esportes e jogos
        "yakyuu": "baseball",
        "sakkaa": "futebol",
        "basuke": "basquete",
        "suiei": "natação",
        "gorufu": "golfe",
        "tenisu": "tênis",
        "sumo": "sumô",
        "karate": "karatê",
        "judo": "judô",
        "kendo": "kendô",
        "shougi": "shogi",
        "go": "go",
        "mahjong": "mahjong",
        "pachinko": "pachinko",
        
        # Romance e relacionamentos
        "suki": "gosto",
        "daisuki": "amo muito",
        "aishiteru": "te amo",
        "koibito": "namorado",
        "kare": "namorado",
        "kanojo": "namorada",
        "tsukiau": "namorar",
        "kokuhaku": "confissão",
        "furareta": "levei um fora",
        "furimashita": "dei um fora",
        "kisu": "beijo",
        "deto": "encontro",
        "kekkon": "casamento",
        "rikon": "divórcio",
        
        # Expressões idiomáticas
        "ganbatte": "boa sorte",
        "ganbare": "vai lá",
        "faito": "lute",
        "omedeto": "parabéns",
        "omedetou": "parabéns",
        "zannen": "que pena",
        "shouganai": "não tem jeito",
        "shikata ga nai": "não tem jeito",
        "maa maa": "mais ou menos",
        "sou desu ne": "pois é",
        "naruhodo": "entendo",
        "sasuga": "como esperado",
        "yappari": "como eu pensava",
        "yahari": "como eu pensava",
        "masaka": "não pode ser",
        "uso deshou": "não acredito",
    }

    # Schema v2: termos com metadados (confidence derivada de source + count)
    SCHEMA_VERSION = 2

    def __init__(self, logger=None, storage_dir: Optional[str] = None):
        """
        Inicializa o gerenciador de glossário.
        
        Args:
            logger: Logger para registrar operações (opcional)
            storage_dir: Diretório para glossários por série (default: glossaries/ ao lado do script)
        """
        self.logger = logger
        self.global_glossary = self.GLOBAL_GLOSSARY.copy()
        
        # Diretório de persistência (glossaries/series_{id}.json)
        self._storage_dir = Path(storage_dir) if storage_dir else Path(__file__).resolve().parent.parent / 'glossaries'
        
        # Locks: um por series_id para evitar corrupção em escrita concorrente
        self._series_locks: Dict[int, threading.Lock] = {}
        self._series_locks_mutex = threading.Lock()
        
        # Dados v2 em memória por série (após load_from_disk ou fetch)
        self._series_v2: Dict[int, dict] = {}
        
        # Cache de glossários por série (com TTL 24h) — flat dict para compatibilidade
        self.series_glossary_cache: Dict[int, Dict[str, str]] = {}
        self.series_cache_timestamps: Dict[int, float] = {}
        self.cache_ttl_seconds = 24 * 3600  # 24 horas
        
        self._log('info', f'GlossaryManager inicializado com {len(self.global_glossary)} termos globais')

    def _log(self, level: str, message: str):
        """Log de mensagens"""
        if self.logger:
            self.logger.log(level, f'[GlossaryManager] {message}')
        else:
            print(f'[{level.upper()}] {message}')

    def _is_cache_valid(self, series_id: int) -> bool:
        """Verifica se o cache da série ainda é válido (TTL 24h)"""
        if series_id not in self.series_cache_timestamps:
            return False
        elapsed = time.time() - self.series_cache_timestamps[series_id]
        return elapsed < self.cache_ttl_seconds

    def _get_series_lock(self, series_id: int) -> threading.Lock:
        """Lock por série para escrita atômica."""
        with self._series_locks_mutex:
            if series_id not in self._series_locks:
                self._series_locks[series_id] = threading.Lock()
            return self._series_locks[series_id]

    def _path_for_series(self, series_id: int) -> Path:
        return self._storage_dir / f'series_{series_id}.json'

    def load_from_disk(self, series_id: int) -> Optional[dict]:
        """
        Carrega glossário v2 do disco. Escrita atômica não aplicável (só leitura).
        Retorna None se arquivo não existir ou estiver corrompido.
        """
        path = self._path_for_series(series_id)
        if not path.exists():
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            self._log('warn', f'Glossário corrompido ou ilegível para série {series_id}: {e}')
            return None
        # Migrar v1 -> v2 se necessário
        if data.get('schema_version') != self.SCHEMA_VERSION:
            data = self._migrate_v1_to_v2(data, series_id)
        return data

    def save_to_disk(self, series_id: int, data: dict) -> bool:
        """Salva glossário v2 em disco com escrita atômica (tmp + replace)."""
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        path = self._path_for_series(series_id)
        tmp = path.with_suffix(path.suffix + '.tmp')
        lock = self._get_series_lock(series_id)
        with lock:
            try:
                data['schema_version'] = self.SCHEMA_VERSION
                data['updated_at'] = datetime.utcnow().isoformat() + 'Z'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    if hasattr(os, 'fsync'):
                        f.fsync()
                os.replace(tmp, path)
                return True
            except OSError as e:
                self._log('warn', f'Sem permissão ou erro ao salvar glossário série {series_id}: {e}')
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                return False

    def _migrate_v1_to_v2(self, data: dict, series_id: int) -> dict:
        """Converte schema v1 (terms: {k: v}) para v2 (terms: {k: {value, source, count, pinned, last_seen}})."""
        terms_raw = data.get('terms', {})
        if not terms_raw:
            return {
                'schema_version': self.SCHEMA_VERSION,
                'terms': {},
                'episodes_scanned': data.get('episodes_scanned', 0),
                'updated_at': datetime.utcnow().isoformat() + 'Z',
            }
        terms_v2 = {}
        now = datetime.utcnow().isoformat() + 'Z'
        for k, v in terms_raw.items():
            if isinstance(v, dict):
                terms_v2[k] = v
            else:
                terms_v2[k] = {
                    'value': str(v),
                    'source': 'migrated',
                    'count': 1,
                    'pinned': False,
                    'last_seen': now,
                }
        return {
            'schema_version': self.SCHEMA_VERSION,
            'terms': terms_v2,
            'episodes_scanned': data.get('episodes_scanned', 0),
            'updated_at': now,
        }

    @staticmethod
    def _derived_confidence(term_meta: dict) -> float:
        """Confiança derivada: pinned=1; senão por source (sonarr/anilist > llm_prescan > auto_track) + count."""
        if term_meta.get('pinned'):
            return 1.0
        source = term_meta.get('source', 'auto_track')
        base = {'sonarr': 0.9, 'anilist': 0.85, 'llm_prescan': 0.75, 'manual': 0.95, 'migrated': 0.7}.get(source, 0.5)
        count = term_meta.get('count', 1)
        boost = min(0.2, count * 0.02)
        return min(1.0, base + boost)

    def get_budgeted_glossary(self, series_id: Optional[int], max_terms: int = 200) -> Dict[str, str]:
        """
        Retorna glossário flat (k -> v) priorizado: pinned primeiro, depois confidence/count, truncado a max_terms.
        Usado para injetar no prompt. Inclui termos globais que couberem.
        """
        result = {}
        # 1) Termos da série (v2) ordenados por pinned, depois confidence, depois count
        if series_id is not None and series_id in self._series_v2:
            v2 = self._series_v2[series_id]
            terms = v2.get('terms', {})
            items = []
            for k, meta in terms.items():
                v = meta.get('value', meta) if isinstance(meta, dict) else meta
                conf = self._derived_confidence(meta if isinstance(meta, dict) else {})
                items.append((k, v, meta.get('pinned', False), conf, meta.get('count', 0) if isinstance(meta, dict) else 0))
            items.sort(key=lambda x: (-x[2], -x[3], -x[4]))  # pinned desc, confidence desc, count desc
            for k, v, _, _, _ in items:
                if len(result) >= max_terms:
                    break
                result[k] = v
        # 2) Se ainda couber, preencher com cache flat (compatibilidade) ou nada
        if series_id is not None and series_id in self.series_glossary_cache and len(result) < max_terms:
            for k, v in self.series_glossary_cache[series_id].items():
                if k not in result:
                    result[k] = v
                    if len(result) >= max_terms:
                        break
        # 3) Globais até completar budget
        remaining = max_terms - len(result)
        if remaining > 0:
            for k, v in sorted(self.global_glossary.items()):
                if k not in result:
                    result[k] = v
                    remaining -= 1
                    if remaining <= 0:
                        break
        return result

    def fetch_from_sonarr_and_anilist(
        self,
        sonarr_url: str,
        api_key: str,
        series_id: int
    ) -> Dict[str, str]:
        """
        Carrega glossário da série: primeiro do disco; se não existir, busca Sonarr/AniList e salva.
        Retorna dict flat para compatibilidade (priorizado por get_budgeted_glossary).
        """
        # 1) Tentar disco primeiro
        disk = self.load_from_disk(series_id)
        if disk is not None:
            self._series_v2[series_id] = disk
            self.series_glossary_cache[series_id] = self.get_budgeted_glossary(series_id)
            self.series_cache_timestamps[series_id] = time.time()
            self._log('debug', f'Glossário série {series_id} carregado do disco ({len(disk.get("terms", {}))} termos)')
            return self.series_glossary_cache[series_id]

        # 2) Cache em memória válido?
        if self._is_cache_valid(series_id):
            self._log('debug', f'Usando cache para série {series_id}')
            return self.series_glossary_cache.get(series_id, {})

        # 3) Buscar da API e montar v2
        series_glossary = {}

        try:
            # 1. Buscar dados da série no Sonarr
            sonarr_url = sonarr_url.rstrip('/')
            headers = {'X-Api-Key': api_key}
            
            response = requests.get(
                f"{sonarr_url}/api/v3/series/{series_id}",
                headers=headers,
                timeout=10
            )
            
            if response.status_code != 200:
                self._log('warn', f'Sonarr retornou {response.status_code} para série {series_id}')
                return series_glossary

            series_data = response.json()
            title = series_data.get('title', '')
            overview = series_data.get('overview', '')
            alternative_titles = series_data.get('alternativeTitles', [])
            tvdb_id = series_data.get('tvdbId')
            
            self._log('info', f'Carregado série: {title} (ID: {series_id})')

            # 2. Extrair nomes próprios do overview (regex: palavras capitalizadas de 4+ caracteres)
            if overview:
                # Buscar palavras capitalizadas (potenciais nomes próprios)
                proper_nouns = re.findall(r'\b[A-Z][a-zA-Z0-9 ]{3,}\b', overview)
                for noun in set(proper_nouns):  # Remover duplicatas
                    # Nomes próprios geralmente não são traduzidos
                    series_glossary[noun.lower()] = noun
            
            # 3. Adicionar títulos alternativos
            for alt_title in alternative_titles:
                if isinstance(alt_title, dict):
                    alt = alt_title.get('title', '')
                else:
                    alt = str(alt_title)
                
                if alt and alt.lower() != title.lower():
                    # Versões alternativas geralmente não traduzem
                    series_glossary[alt.lower()] = alt

            # 4. Buscar sinônimos no AniList via GraphQL
            try:
                anilist_query = """
                query($search: String, $tvdbId: Int) {
                  Media(search: $search, type: ANIME) {
                    title { english romaji native }
                    synonyms
                    description
                    genres
                  }
                }
                """
                
                variables = {"search": title}
                if tvdb_id:
                    variables["tvdbId"] = tvdb_id
                
                anilist_response = requests.post(
                    'https://graphql.anilist.co',
                    json={'query': anilist_query, 'variables': variables},
                    timeout=10
                )
                
                if anilist_response.status_code == 200:
                    anilist_data = anilist_response.json()
                    if 'data' in anilist_data and 'Media' in anilist_data['data']:
                        media = anilist_data['data']['Media']
                        
                        # Adicionar sinônimos (sem traduzir)
                        if media.get('synonyms'):
                            for synonym in media['synonyms']:
                                if synonym:
                                    series_glossary[synonym.lower()] = synonym
                        
                        # Adicionar gêneros
                        if media.get('genres'):
                            for genre in media['genres']:
                                if genre:
                                    series_glossary[genre.lower()] = genre
                        
                        self._log('info', f'AniList retornou {len(media.get("synonyms", []))} sinônimos')
                else:
                    self._log('warn', f'AniList retornou {anilist_response.status_code}')
            
            except requests.Timeout:
                self._log('warn', 'Timeout ao consultar AniList')
            except Exception as e:
                self._log('warn', f'Erro ao consultar AniList: {e}')

        except requests.Timeout:
            self._log('error', f'Timeout ao conectar ao Sonarr para série {series_id}')
        except Exception as e:
            self._log('error', f'Erro ao buscar dados da série {series_id}: {e}')

        # Persistir em v2 e disco
        now = datetime.utcnow().isoformat() + 'Z'
        terms_v2 = {}
        for k, v in series_glossary.items():
            terms_v2[k] = {'value': v, 'source': 'sonarr', 'count': 1, 'pinned': False, 'last_seen': now}
        v2_data = {
            'schema_version': self.SCHEMA_VERSION,
            'terms': terms_v2,
            'episodes_scanned': 0,
            'updated_at': now,
        }
        self._series_v2[series_id] = v2_data
        self.save_to_disk(series_id, v2_data)
        self.series_glossary_cache[series_id] = self.get_budgeted_glossary(series_id)
        self.series_cache_timestamps[series_id] = time.time()
        
        self._log('info', f'Glossário da série {series_id} contém {len(series_glossary)} termos')
        return self.series_glossary_cache[series_id]

    def apply_to_text(self, text: str, series_glossary: Optional[Dict[str, str]] = None) -> str:
        """
        Aplica substituições do glossário ao texto (case-insensitive).
        Prioriza série_glossary > glossário global.
        
        Args:
            text: Texto a processar
            series_glossary: Glossário específico da série (opcional)
            
        Returns:
            Texto com substituições aplicadas
        """
        if not text:
            return text

        result = text
        
        # Combinar glossários (série sobrescreve global)
        combined_glossary = {**self.global_glossary}
        if series_glossary:
            combined_glossary.update(series_glossary)

        # Aplicar substituições com re.sub (case-insensitive)
        # Usar word boundaries para evitar substituições parciais
        for en_term, pt_term in combined_glossary.items():
            # Pattern: word boundary + termo + word boundary (case-insensitive)
            pattern = r'\b' + re.escape(en_term) + r'\b'
            replacement = pt_term
            
            # Substituição preservando case quando possível
            def replace_func(match):
                original = match.group(0)
                if not replacement:
                    return original
                if original.isupper():
                    return replacement.upper()
                elif original[0].isupper() and len(original) > 1:
                    return replacement[0].upper() + replacement[1:]
                else:
                    return replacement

            result = re.sub(pattern, replace_func, result, flags=re.IGNORECASE)

        return result

    def get_prompt_injection(self, series_glossary: Optional[Dict[str, str]] = None) -> str:
        """
        Retorna string de injeção de prompt para o Ollama com termos do glossário.
        
        Args:
            series_glossary: Glossário específico da série (opcional)
            
        Returns:
            String formatada para injetar no system_prompt
        """
        combined_glossary = {**self.global_glossary}
        if series_glossary:
            combined_glossary.update(series_glossary)

        # Criar lista formatada
        glossary_items = []
        for en_term, pt_term in sorted(combined_glossary.items()):
            glossary_items.append(f"- {en_term} → {pt_term}")

        glossary_text = "\n".join(glossary_items)
        
        injection = f"""GLOSSÁRIO OBRIGATÓRIO - Use exatamente estes termos:
{glossary_text}

REGRA CRÍTICA: Mantenha estes termos SEM TRADUZIR em suas respostas. Se encontrar um destes termos na legenda, NÃO TRADUZA."""

        return injection

    def clear_cache(self, series_id: Optional[int] = None):
        """
        Limpa o cache de glossários em memória (não apaga arquivos em disco).
        
        Args:
            series_id: Se None, limpa tudo. Senão, limpa apenas a série especificada.
        """
        if series_id is None:
            self.series_glossary_cache.clear()
            self.series_cache_timestamps.clear()
            self._series_v2.clear()
            self._log('info', 'Cache de glossários limpo completamente')
        else:
            self.series_glossary_cache.pop(series_id, None)
            self.series_cache_timestamps.pop(series_id, None)
            self._series_v2.pop(series_id, None)
            self._log('info', f'Cache limpo para série {series_id}')

    def get_statistics(self) -> Dict:
        """Retorna estatísticas do gerenciador"""
        disk_count = 0
        try:
            if self._storage_dir.exists():
                disk_count = sum(1 for p in self._storage_dir.glob('series_*.json') if p.is_file())
        except OSError:
            pass
        return {
            'global_terms': len(self.global_glossary),
            'cached_series': len(self.series_glossary_cache),
            'series_on_disk': disk_count,
            'cache_ttl_hours': self.cache_ttl_seconds / 3600,
        }

    # ──── Glossário automático bidirecional (Fase 3b) ────

    def _is_safe_suggested_term(self, term: str, translation: str) -> bool:
        """Anti-envenenamento: ignora termos muito curtos, stopwords, traduções longas/frases."""
        term_clean = term.strip().lower()
        if len(term_clean) < 3:
            return False
        if term_clean in _STOPWORDS_GLOSSARY:
            return False
        if len(translation) > 80 or translation.count(' ') > 10:
            return False
        return True

    def merge_suggested_terms(self, series_id: int, suggested: Dict[str, str], min_occurrences: int = 3):
        """
        Merge termos sugeridos pelo TranslationJob no glossário da série (v2).
        Aplica filtros anti-envenenamento. Salva em disco ao final.
        """
        if not suggested:
            return
        lock = self._get_series_lock(series_id)
        with lock:
            if series_id not in self._series_v2:
                self._series_v2[series_id] = {
                    'schema_version': self.SCHEMA_VERSION,
                    'terms': {},
                    'episodes_scanned': 0,
                    'updated_at': datetime.utcnow().isoformat() + 'Z',
                }
            v2 = self._series_v2[series_id]
            terms = v2.setdefault('terms', {})
            now = datetime.utcnow().isoformat() + 'Z'
            added = 0
            for term, translation in suggested.items():
                if not self._is_safe_suggested_term(term, translation):
                    continue
                term_lower = term.strip().lower()
                existing = terms.get(term_lower)
                if existing is None:
                    terms[term_lower] = {'value': translation.strip(), 'source': 'auto_track', 'count': min_occurrences, 'pinned': False, 'last_seen': now}
                    added += 1
                elif isinstance(existing, dict) and existing.get('source') == 'auto_track':
                    # Atualizar count e value se vieram de job com boa ocorrência
                    terms[term_lower] = {**existing, 'value': translation.strip(), 'count': max(existing.get('count', 1), min_occurrences), 'last_seen': now}
            v2['episodes_scanned'] = v2.get('episodes_scanned', 0) + 1
            if added > 0:
                self._log('info', f'Glossário automático: +{added} termos para série {series_id}')
            self.series_glossary_cache[series_id] = self.get_budgeted_glossary(series_id)
            self.series_cache_timestamps[series_id] = time.time()
            self.save_to_disk(series_id, v2)

    def get_prompt_injection_compact(self, series_glossary: Optional[Dict[str, str]] = None,
                                      max_terms: int = 50) -> str:
        """
        Versão compacta da injeção de glossário para caber no budget de tokens.
        Prioriza termos da série sobre globais.
        """
        items = []
        
        # Primeiro: termos da série (prioridade)
        if series_glossary:
            for k, v in sorted(series_glossary.items()):
                items.append(f"{k} → {v}")
                if len(items) >= max_terms:
                    break

        # Depois: globais (se couber)
        remaining = max_terms - len(items)
        if remaining > 0:
            count = 0
            for k, v in sorted(self.global_glossary.items()):
                if series_glossary and k in series_glossary:
                    continue
                items.append(f"{k} → {v}")
                count += 1
                if count >= remaining:
                    break

        if not items:
            return ""

        return (
            "GLOSSÁRIO (use exatamente estes termos):\n"
            + ", ".join(items)
            + "\nNÃO traduza estes termos."
        )

    def generate_glossary_with_llm(
        self,
        series_title: str,
        subtitle_lines: List[str],
        ollama_url: str,
        model: str,
        stop_check: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, str]:
        """
        Usa modelo leve para extrair nomes próprios e termos das linhas de legenda.
        Parser resiliente: JSON primeiro, depois fallback linha a linha.
        Retorna dict flat {termo_lower: valor_preservado}. Vazio se falhar ou stop_check retornar True.
        """
        if not subtitle_lines or not ollama_url or not model:
            return {}
        if stop_check and stop_check():
            return {}
        sample = '\n'.join(subtitle_lines[:80])
        prompt = (
            f'Analyze these subtitle lines and extract ALL proper nouns (character names, places, techniques, titles). '
            f'Reply ONLY with a JSON object: {{"OriginalName": "PreservedName", ...}}. '
            f'Preserve the original form (do not translate).\n\nLines:\n{sample}'
        )
        url = ollama_url.rstrip('/')
        try:
            resp = requests.post(
                f'{url}/api/generate',
                json={'model': model, 'prompt': prompt, 'stream': False},
                timeout=120,
            )
            if stop_check and stop_check():
                return {}
            if resp.status_code != 200:
                self._log('warn', f'Pre-scan LLM retornou {resp.status_code}')
                return {}
            out = resp.json()
            text = out.get('response', '')
        except requests.RequestException as e:
            self._log('warn', f'Pre-scan LLM falhou: {e}')
            return {}
        # Parser resiliente
        parsed = self._parse_llm_glossary_response(text)
        if parsed:
            self._log('info', f'Pre-scan extraiu {len(parsed)} termos para "{series_title}"')
        return parsed

    def merge_prescan_terms(self, series_id: int, prescan_flat: Dict[str, str]) -> None:
        """Mescla termos do pre-scan LLM no glossário da série (source=llm_prescan) e marca episodes_scanned=1."""
        if not prescan_flat:
            return
        lock = self._get_series_lock(series_id)
        with lock:
            if series_id not in self._series_v2:
                self._series_v2[series_id] = {
                    'schema_version': self.SCHEMA_VERSION,
                    'terms': {},
                    'episodes_scanned': 0,
                    'updated_at': datetime.utcnow().isoformat() + 'Z',
                }
            v2 = self._series_v2[series_id]
            terms = v2.setdefault('terms', {})
            now = datetime.utcnow().isoformat() + 'Z'
            for k, v in prescan_flat.items():
                key = k.strip().lower()
                if len(key) < 2:
                    continue
                if key not in terms:
                    terms[key] = {'value': v.strip(), 'source': 'llm_prescan', 'count': 1, 'pinned': False, 'last_seen': now}
            v2['episodes_scanned'] = 1
            self.series_glossary_cache[series_id] = self.get_budgeted_glossary(series_id)
            self.series_cache_timestamps[series_id] = time.time()
            self.save_to_disk(series_id, v2)
            self._log('info', f'Pre-scan: {len(prescan_flat)} termos incorporados para série {series_id}')

    def _parse_llm_glossary_response(self, text: str) -> Dict[str, str]:
        """Tenta json.loads; senão extrai pares key -> value ou "key": "value" por linha."""
        text = text.strip()
        # Tentar extrair bloco JSON
        start = text.find('{')
        end = text.rfind('}') + 1
        if start >= 0 and end > start:
            block = text[start:end]
            try:
                data = json.loads(block)
                if isinstance(data, dict):
                    return {str(k).strip().lower(): str(v).strip() for k, v in data.items() if k and v}
            except json.JSONDecodeError:
                pass
        # Fallback: linhas com "key": "value" ou key -> value
        result = {}
        for line in text.splitlines():
            line = line.strip()
            for sep in ('->', ':', '→'):
                if sep in line:
                    parts = line.split(sep, 1)
                    if len(parts) == 2:
                        k = parts[0].strip().strip('"\'')
                        v = parts[1].strip().strip('"\'')
                        if len(k) >= 2 and len(v) >= 1:
                            result[k.lower()] = v
                    break
        return result
