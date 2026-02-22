"""
Robust subtitle quality validation
Enhanced with semantic inversion detection, pronoun checks,
and relaxed colloquialism thresholds for PT-BR.
"""
import re
from pathlib import Path
from typing import Tuple, Optional
try:
    import langdetect
    from langdetect import detect, LangDetectError
except ImportError:
    langdetect = None

class SubtitleQualityValidator:
    def __init__(self, logger=None):
        self.logger = logger
        
        # Negation words for semantic inversion detection
        self._english_negations = {
            "not", "n't", "never", "no", "neither", "nor", "nobody",
            "nothing", "nowhere", "hardly", "barely", "scarcely",
            "don't", "doesn't", "didn't", "won't", "wouldn't",
            "can't", "cannot", "couldn't", "shouldn't", "isn't",
            "aren't", "wasn't", "weren't", "haven't", "hasn't",
            "hadn't", "mustn't", "hate", "refuse", "deny",
        }
        self._portuguese_negations = {
            "não", "nunca", "nenhum", "nenhuma", "nem", "ninguém",
            "nada", "jamais", "tampouco", "sequer", "odeio",
            "recuso", "nego", "impossível", "incapaz",
        }
        
        # Pronoun mapping for gender mismatch detection
        self._pronoun_map_en_to_pt = {
            "she": {"ela", "dela", "lhe"},
            "her": {"ela", "dela", "lhe", "sua", "a"},
            "he": {"ele", "dele", "lhe"},
            "him": {"ele", "dele", "lhe", "o"},
            "they": {"eles", "elas", "deles", "delas"},
            "them": {"eles", "elas", "deles", "delas", "os", "as", "lhes"},
        }
        
        # Expanded Portuguese words list (much more comprehensive)
        self.portuguese_words = {
            # Common words
            'que', 'não', 'uma', 'com', 'para', 'você', 'ele', 'ela', 'isso', 'mais',
            'muito', 'bem', 'aqui', 'onde', 'quando', 'como', 'por', 'mas', 'então',
            'agora', 'ainda', 'já', 'só', 'também', 'até', 'depois', 'antes', 'sobre',
            # Verbs (common tenses)
            'é', 'são', 'foi', 'foram', 'ser', 'está', 'estão', 'estou', 'estamos',
            'tem', 'tenho', 'temos', 'tinha', 'tinha', 'tinha', 'vou', 'vai', 'vamos',
            'fiz', 'fez', 'fizemos', 'fazer', 'faço', 'faz', 'fazem', 'feito',
            'pode', 'podem', 'posso', 'puder', 'poderia', 'poder', 'pude', 'possa',
            'devo', 'deve', 'devem', 'devemos', 'dever', 'devia', 'deviam',
            'preciso', 'precisa', 'precisam', 'precisamos', 'precisar',
            'quero', 'quer', 'querem', 'queremos', 'querer', 'quis', 'quisemos',
            'penso', 'pensa', 'pensam', 'pensamos', 'pensar', 'pensava', 'pensavam',
            'digo', 'diz', 'dizem', 'dizemos', 'dizer', 'disse', 'dissemos',
            'vejo', 'vê', 'veem', 'vemos', 'ver', 'vi', 'vimos', 'via', 'viam',
            'dado', 'dada', 'dados', 'dadas', 'dar', 'dou', 'da', 'dão',
            'meu', 'minha', 'meus', 'minhas', 'nosso', 'nossa', 'nossos', 'nossas',
            'seu', 'sua', 'seus', 'suas', 'dele', 'dela', 'deles', 'delas',
            'isso', 'isto', 'aquilo', 'este', 'esse', 'aquele', 'esta', 'essa', 'aquela',
            'estes', 'esses', 'aqueles', 'estas', 'essas', 'aquelas',
            # Common adjectives
            'bom', 'boa', 'bons', 'boas', 'ruim', 'ruins', 'grande', 'pequeno',
            'novo', 'velho', 'alto', 'baixo', 'longo', 'curto', 'forte', 'fraco',
            'rápido', 'lento', 'fácil', 'difícil', 'bonito', 'feio', 'real', 'falso',
            'certo', 'errado', 'claro', 'escuro', 'quente', 'frio', 'seco', 'molhado',
            # Common nouns
            'homem', 'mulher', 'pessoa', 'filho', 'filha', 'pai', 'mãe', 'avó', 'avô',
            'amigo', 'amiga', 'família', 'casa', 'tempo', 'dia', 'noite', 'hora',
            'mundo', 'vida', 'morte', 'amor', 'ódio', 'medo', 'esperança', 'verdade',
            'mentira', 'coisa', 'lugar', 'maneira', 'forma', 'tipo', 'jeito', 'modo',
            'corpo', 'cabeça', 'coração', 'mão', 'pé', 'olho', 'boca', 'ouvido',
            'palavra', 'frase', 'pergunta', 'resposta', 'história', 'livro', 'filme',
            'escola', 'trabalho', 'noite', 'manhã', 'tarde', 'semana', 'ano', 'mês',
            'número', 'cor', 'som', 'nome', 'idade', 'peso', 'altura', 'distância',
            # Prepositions and particles
            'em', 'ao', 'a', 'de', 'do', 'da', 'dos', 'das', 'e', 'ou', 'nem',
            'se', 'sem', 'sob', 'entre', 'durante', 'dentro', 'fora', 'junto',
            'contra', 'através', 'conforme', 'segundo', 'perante', 'salvo', 'exceto',
            # Adverbs
            'muito', 'pouco', 'bastante', 'demais', 'menos', 'mais', 'tão', 'quão',
            'sim', 'não', 'talvez', 'certamente', 'provavelmente', 'sempre', 'nunca',
            'frequentemente', 'raramente', 'aqui', 'ali', 'lá', 'cá', 'acolá',
            'hoje', 'ontem', 'amanhã', 'cedo', 'tarde', 'cedo', 'devagar', 'rápido',
            # Numbers
            'um', 'uma', 'dois', 'duas', 'três', 'quatro', 'cinco', 'seis',
            'sete', 'oito', 'nove', 'dez', 'onze', 'doze', 'treze', 'vinte',
            'trinta', 'quarenta', 'cinquenta', 'cem', 'mil', 'milhão',
            # Other common words
            'há', 'havia', 'há', 'haverá', 'haveria', 'havendo', 'houve',
            'seja', 'sejas', 'sejamos', 'sejam', 'fosse', 'fosses', 'fôssemos',
            'nada', 'tudo', 'algo', 'alguém', 'ninguém', 'outro', 'mesmo', 'próprio',
            'único', 'último', 'primeiro', 'próximo', 'anterior', 'posterior',
        }
        
        # Common English words that shouldn't appear in Portuguese
        self.english_words = {
            'the', 'and', 'you', 'that', 'was', 'for', 'are', 'with', 'his', 'they',
            'have', 'this', 'will', 'your', 'from', 'can', 'said', 'each', 'which',
            'about', 'would', 'there', 'their', 'what', 'when', 'make', 'like', 'just',
            'time', 'know', 'take', 'people', 'year', 'work', 'back', 'call', 'hand',
            'high', 'keep', 'last', 'long', 'make', 'need', 'part', 'right', 'seem',
            'tell', 'think', 'turn', 'want', 'way', 'week', 'well', 'year'
        }
    
    def validate_subtitle_file(self, file_path):
        """Validate entire subtitle file quality"""
        try:
            file_path = Path(file_path)
            if not file_path.exists():
                return False, "File does not exist"
            
            # Read file content
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except UnicodeDecodeError:
                try:
                    with open(file_path, 'r', encoding='cp1252') as f:
                        content = f.read()
                except:
                    return False, "Cannot read file with any encoding"
            
            return self.validate_subtitle_content(content)
            
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    def validate_subtitle_content(self, content):
        """Validate subtitle content quality"""
        if not content or len(content.strip()) < 20:  # Reduced from 50
            return False, "Content too short"
        
        # Extract text lines (skip timestamps, numbers, etc.)
        text_lines = self._extract_text_lines(content)
        
        if len(text_lines) < 1:  # Reduced from 3 - allow single line subtitles
            return False, "Too few subtitle lines"
        
        # Join all text for analysis
        full_text = ' '.join(text_lines)
        
        # Check minimum useful characters - more permissive
        useful_chars = sum(1 for c in full_text if c.isalpha())
        if useful_chars < 10:  # Reduced from 100
            return False, f"Too few useful characters: {useful_chars}"
        
        # Primary check: Language detection (most reliable)
        detected_lang = self._detect_language(full_text)
        
        # If language detection says Portuguese, trust it
        if detected_lang == 'pt':
            return True, "Quality validation passed"
        
        # Secondary check: Word-based validation (fallback if detection fails or is unsure)
        words = re.findall(r'\b\w+\b', full_text.lower())
        if len(words) < 5:  # Reduced from 20
            return False, "Too few words"
        
        # Use enhanced Portuguese word detection
        portuguese_score = self._calculate_portuguese_score(full_text, words)
        
        # More permissive threshold: 20% Portuguese words or confident detection
        if portuguese_score >= 0.20:
            return True, "Quality validation passed"
        
        # If detection suggested Portuguese but score is low, still accept it
        if detected_lang in ['pt', 'es']:  # Portuguese or Spanish (similar)
            if portuguese_score >= 0.10:  # Lower threshold for detected language
                return True, "Quality validation passed"
        
        # Final check: Check for translation artifacts
        if self._has_translation_artifacts(full_text):
            return False, "Contains translation artifacts"
        
        # If we get here, it didn't pass validation
        return False, f"Quality validation failed: Portuguese score {portuguese_score:.2%}, Detected language: {detected_lang}"
    
    def _detect_language(self, text):
        """Detect language using langdetect with better error handling"""
        if not langdetect or len(text) < 50:
            return 'unknown'
        
        try:
            lang = detect(text)
            return lang
        except LangDetectError:
            return 'unknown'
        except Exception:
            return 'unknown'
    
    def _calculate_portuguese_score(self, full_text, words):
        """Calculate Portuguese language score using multiple methods"""
        if not words or len(words) == 0:
            return 0
        
        # Method 1: Portuguese word list matching (more comprehensive now)
        portuguese_count = sum(1 for word in words if word in self.portuguese_words)
        portuguese_ratio = portuguese_count / len(words)
        
        # Method 2: English word counting (negative signal)
        english_count = sum(1 for word in words if word in self.english_words)
        english_ratio = english_count / len(words)
        
        # Method 3: Portuguese-specific patterns
        pt_patterns = self._count_portuguese_patterns(full_text)
        
        # Combine scores
        score = portuguese_ratio * 0.6  # 60% weight on word matching
        score += (1 - english_ratio) * 0.2  # 20% weight on not being English
        score += min(0.2, pt_patterns * 0.01) * 0.2  # 20% weight on patterns
        
        return score
    
    def _count_portuguese_patterns(self, text):
        """Count Portuguese-specific linguistic patterns"""
        count = 0
        text_lower = text.lower()
        
        # Portuguese-specific patterns
        patterns = {
            r'\bção\b': 3,  # -ção ending (very Portuguese)
            r'\bdade\b': 2,  # -dade ending (Portuguese)
            r'\b\w+mente\b': 2,  # Adverbs ending in -mente (Portuguese style)
            r'\bvocê\b': 3,  # "você" is very Portuguese
            r'\bnão\b': 2,  # "não" (negation in Portuguese)
            r'\bé\b': 1,  # Common verb "é"
            r'\bestá\b': 1,  # Common verb "está"
            r'\btem\b': 1,  # Common verb "tem"
            r'\bfoi\b': 1,  # Common verb "foi"
        }
        
        for pattern, weight in patterns.items():
            matches = len(re.findall(pattern, text_lower))
            count += matches * weight
        
        return count
    
    def validate_translation_quality(self, original_content, translated_content):
        """Validate translation quality by comparing original and translated"""
        if not original_content or not translated_content:
            return False, "Empty content"
        
        original_lines = self._extract_text_lines(original_content)
        translated_lines = self._extract_text_lines(translated_content)
        
        # Allow for small differences in line count (glossary may affect line breaks)
        # Only fail if difference is more than 10% or more than 5 lines
        line_diff = abs(len(original_lines) - len(translated_lines))
        max_allowed_diff = max(5, int(len(original_lines) * 0.1))
        
        if line_diff > max_allowed_diff:
            return False, f"Line count mismatch: {len(original_lines)} vs {len(translated_lines)}"
        
        if len(original_lines) == 0:
            return False, "No lines to validate"
        
        # Check percentage of lines that were actually translated
        # Use the minimum length to avoid IndexError when line counts differ
        min_lines = min(len(original_lines), len(translated_lines))
        changed_lines = 0
        untranslated_count = 0
        
        for i in range(min_lines):
            orig_clean = original_lines[i].strip()
            trans_clean = translated_lines[i].strip()
            
            if orig_clean != trans_clean and len(trans_clean) > 0:
                changed_lines += 1
            elif orig_clean == trans_clean:
                untranslated_count += 1
        
        change_ratio = changed_lines / min_lines if min_lines > 0 else 0
        untranslated_ratio = untranslated_count / min_lines if min_lines > 0 else 0
        
        # More intelligent checking:
        # If too many lines weren't translated (unchanged), it's suspicious
        if untranslated_ratio > 0.7:  # More than 70% unchanged
            return False, f"Too many untranslated lines: {untranslated_ratio:.2%}"
        
        # Minimum translation threshold (relaxed: only 5% need to be translated)
        if change_ratio < 0.05:  # Reduced from 0.1 (5% vs 10%)
            return False, f"Too few lines translated: {change_ratio:.2%}"
        
        # Validate the translated content itself
        is_valid, message = self.validate_subtitle_content(translated_content)
        if not is_valid:
            # Only fail if it's a critical issue, not just "too few lines"
            if "Too few subtitle lines" not in message and "Too few words" not in message:
                return False, message
        
        return True, "Translation quality validation passed"
    
    def _extract_text_lines(self, content):
        """Extract actual text lines from subtitle content"""
        lines = content.split('\n')  # Fixed: use '\n' not '\\n'
        text_lines = []
        
        for line in lines:
            line = line.strip()
            
            # Skip empty lines
            if not line:
                continue
            
            # Skip sequence numbers
            if line.isdigit():
                continue
            
            # Skip timestamps
            if '-->' in line:
                continue
            
            # Skip ASS/SSA headers
            if line.startswith('[') and line.endswith(']'):
                continue
            
            # Skip ASS style definitions
            if line.startswith('Style:') or line.startswith('Format:'):
                continue
            
            # Extract text from ASS dialogue lines
            if line.startswith('Dialogue:'):
                parts = line.split(',', 9)
                if len(parts) > 9:
                    text = parts[9]
                    # Remove ASS formatting
                    text = re.sub(r'\\{[^}]*\\}', '', text)
                    if text.strip():
                        text_lines.append(text.strip())
            else:
                # Regular subtitle text
                # Remove HTML tags and ASS formatting
                clean_text = re.sub(r'<[^>]*>', '', line)
                clean_text = re.sub(r'\\{[^}]*\\}', '', clean_text)
                if clean_text.strip():
                    text_lines.append(clean_text.strip())
        
        return text_lines
    
    def _has_translation_artifacts(self, text):
        """Check for common translation artifacts"""
        artifacts = [
            'translation:', 'tradução:', 'note:', 'nota:',
            '[translation]', '[tradução]', 'here is', 'aqui está',
            'the translation is', 'a tradução é'
        ]
        
        text_lower = text.lower()
        return any(artifact in text_lower for artifact in artifacts)
    
    def get_quality_score(self, content):
        """Get a quality score from 0-100"""
        try:
            text_lines = self._extract_text_lines(content)
            if len(text_lines) == 0:
                return 0
            
            full_text = ' '.join(text_lines)
            
            score = 30  # Base score for having content
            
            # Language detection bonus
            detected_lang = self._detect_language(full_text)
            if detected_lang == 'pt':
                score += 30
            elif detected_lang in ['es', 'en']:
                score += 15
            
            # Bonus for length (good translations are usually detailed)
            if len(full_text) > 200:
                score += 10
            if len(full_text) > 500:
                score += 10
            if len(full_text) > 1000:
                score += 10
            
            # Bonus for Portuguese words
            words = re.findall(r'\b\w+\b', full_text.lower())
            if words:
                portuguese_score = self._calculate_portuguese_score(full_text, words)
                score += min(20, int(portuguese_score * 100))
            
            return min(100, max(0, score))
            
        except Exception:
            return 0

    # ──── Validações semânticas (Fase 1c) ────

    def validate_line_translation(
        self,
        original: str,
        translated: str,
    ) -> Tuple[bool, str, float]:
        """
        Validação por linha individual: inversão semântica, pronome, artefatos.
        
        Returns:
            (is_valid, message, confidence_score)
            confidence_score: 0.0 a 1.0 (1.0 = alta confiança)
        """
        if not original or not translated:
            return False, "Empty input", 0.0

        if original.strip().lower() == translated.strip().lower():
            return False, "Translation identical to original", 0.0

        confidence = 1.0
        issues = []

        # 1. Inversão semântica: negação no original mas não na tradução
        neg_check = self._check_semantic_inversion(original, translated)
        if neg_check:
            issues.append(neg_check)
            confidence -= 0.4

        # 2. Inversão de pronome: she/her ↔ ele
        pronoun_check = self._check_pronoun_mismatch(original, translated)
        if pronoun_check:
            issues.append(pronoun_check)
            confidence -= 0.5

        # 3. Razão de comprimento
        len_ratio = len(translated.strip()) / max(1, len(original.strip()))
        if len_ratio < 0.2:
            issues.append(f"Translation too short (ratio={len_ratio:.2f})")
            confidence -= 0.3
        elif len_ratio > 4.0:
            issues.append(f"Translation too long (ratio={len_ratio:.2f})")
            confidence -= 0.2

        # 4. Artefatos comuns de LLM
        artifacts = [
            'translation:', 'tradução:', 'note:', 'nota:', 'here is',
            'aqui está', 'the translation', 'a tradução', 'in portuguese',
            'em português', 'translated:', 'output:', 'result:',
        ]
        trans_lower = translated.strip().lower()
        for artifact in artifacts:
            if trans_lower.startswith(artifact):
                issues.append(f"Artifact detected: '{artifact}'")
                confidence -= 0.5
                break

        # 5. Caracteres CJK na tradução (não deveria ter)
        if re.search(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]', translated):
            issues.append("CJK characters in translation")
            confidence -= 0.6

        confidence = max(0.0, confidence)
        
        if confidence < 0.3:
            return False, "; ".join(issues), confidence
        
        return True, "OK" if not issues else "; ".join(issues), confidence

    def _check_semantic_inversion(self, original: str, translated: str) -> Optional[str]:
        """
        Detecta inversão semântica: se original contém negação
        e tradução não contém negação equivalente.
        """
        orig_words = set(re.findall(r'\b\w+\b', original.lower()))
        trans_words = set(re.findall(r'\b\w+\b', translated.lower()))

        # Checar se há negação no original
        orig_has_neg = bool(orig_words & self._english_negations)
        
        # Contrações: n't
        if any("n't" in w for w in original.lower().split()):
            orig_has_neg = True

        if not orig_has_neg:
            return None

        # Checar se há negação na tradução
        trans_has_neg = bool(trans_words & self._portuguese_negations)
        
        if not trans_has_neg:
            return f"Semantic inversion: original has negation but translation doesn't"
        
        return None

    def _check_pronoun_mismatch(self, original: str, translated: str) -> Optional[str]:
        """
        Detecta troca de pronome de gênero: she/her traduzido como ele.
        """
        orig_lower = original.lower()
        trans_lower = translated.lower()
        orig_words = set(re.findall(r'\b\w+\b', orig_lower))
        trans_words = set(re.findall(r'\b\w+\b', trans_lower))

        # Se original usa pronome feminino
        if orig_words & {"she", "her", "herself", "hers"}:
            # Tradução deve conter pronome feminino, não masculino sozinho
            has_feminine = bool(trans_words & {"ela", "dela", "elha"})
            has_masculine_only = (
                bool(trans_words & {"ele", "dele"})
                and not has_feminine
            )
            if has_masculine_only:
                return "Pronoun mismatch: she/her translated as ele/dele"

        # Se original usa pronome masculino
        if orig_words & {"he", "him", "himself", "his"}:
            has_masculine = bool(trans_words & {"ele", "dele"})
            has_feminine_only = (
                bool(trans_words & {"ela", "dela"})
                and not has_masculine
            )
            if has_feminine_only:
                return "Pronoun mismatch: he/him translated as ela/dela"

        return None

    def is_colloquial_valid(self, translated: str) -> bool:
        """
        Verifica se o uso de coloquialismos é aceitável.
        PT-BR oral legítimo (né, tá, tipo, mano, véi) é válido
        — só rejeita se houver uso ABSURDO (>40% da frase).
        """
        words = translated.lower().split()
        if not words:
            return True
        
        colloquial_words = {"né", "tá", "tipo", "mano", "véi", "cara",
                            "mina", "tô", "cê", "pra", "num", "dum"}
        colloquial_count = sum(1 for w in words if w in colloquial_words)
        ratio = colloquial_count / len(words)
        
        # Relaxado: até 40% é aceitável para diálogo informal
        return ratio <= 0.4