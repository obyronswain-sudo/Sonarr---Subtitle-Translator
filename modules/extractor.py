import subprocess
import os
import json
from pathlib import Path
import chardet
import shutil

class SubtitleExtractor:
    def __init__(self, logger):
        self.logger = logger
        self.mkvinfo_path = self.find_tool_path(['mkvinfo', 'mkvinfo.exe'], [
            r'C:\Program Files\MKVToolNix',
            r'C:\Program Files (x86)\MKVToolNix',
            os.path.expanduser(r'~\AppData\Local\Programs\MKVToolNix'),
        ])
        self.mkvextract_path = self.find_tool_path(['mkvextract', 'mkvextract.exe'], [
            r'C:\Program Files\MKVToolNix',
            r'C:\Program Files (x86)\MKVToolNix',
            os.path.expanduser(r'~\AppData\Local\Programs\MKVToolNix'),
        ])
        self.ffmpeg_path = self.find_tool_path(['ffmpeg', 'ffmpeg.exe'], [
            r'C:\ffmpeg\bin',
            r'C:\Program Files\ffmpeg\bin',
            r'C:\Program Files\ffmpeg-8.0.1-essentials_build\ffmpeg-8.0.1-essentials_build\bin',
        ])
        self.mkvinfo_available = self.mkvinfo_path is not None
        self.mkvextract_available = self.mkvextract_path is not None
        self.ffmpeg_available = self.ffmpeg_path is not None
        if not self.mkvinfo_available or not self.mkvextract_available:
            self.logger.log('error', 'MKVToolNix não encontrado. Instale MKVToolNix.')
        if not self.ffmpeg_available:
            self.logger.log('warning', 'ffmpeg não encontrado. Extração limitada a MKV.')

    def find_tool_path(self, tool_names, search_dirs):
        # Primeiro, tentar via PATH
        for name in tool_names:
            path = shutil.which(name)
            if path:
                return path
        # Depois, procurar em diretórios comuns
        for dir_path in search_dirs:
            for name in tool_names:
                full_path = os.path.join(dir_path, name)
                if os.path.exists(full_path):
                    return full_path
        return None

    def extract_subtitles(self, video_file, preferred_track_id=None):
        # Check for existing extracted subtitles first (skip when a specific track is requested)
        if preferred_track_id is None:
            existing_subs = self.find_existing_subtitles(video_file)
            if existing_subs:
                self.logger.log('info', f'Usando legendas já extraídas para {video_file}')
                return existing_subs

        ext = video_file.suffix.lower()
        self.logger.log('info', f'Extraindo legendas de {video_file} (formato: {ext})')
        if ext == '.mkv':
            return self.extract_mkv_subtitles(video_file, preferred_track_id=preferred_track_id)
        else:
            return self.extract_other_subtitles(video_file)
    
    def find_existing_subtitles(self, video_file, target_lang: str = 'pt-BR'):
        """Find already extracted subtitle files, ignoring already-translated ones."""
        existing = []
        tgt_lower = target_lang.lower()
        for ext in ['.srt', '.ass', '.ssa']:
            for sub_file in video_file.parent.glob(f"{video_file.stem}*{ext}"):
                try:
                    name_lower = sub_file.name.lower()
                    if f'.{tgt_lower}.' in name_lower or name_lower.endswith(f'.{tgt_lower}' + ext):
                        continue
                    if sub_file.exists() and sub_file.stat().st_size > 50:
                        existing.append((sub_file, {'language': 'und'}))
                except OSError:
                    continue
        return existing

    def extract_mkv_subtitles(self, video_file, preferred_track_id=None):
        if not self.mkvinfo_available or not self.mkvextract_available:
            self.logger.log('error', f'MKVToolNix não disponível para {video_file}')
            return []
        # Usar mkvinfo para listar tracks
        try:
            result = subprocess.run([self.mkvinfo_path, '--ui-language', 'en', str(video_file)], capture_output=True, text=True)
            if result.returncode != 0:
                self.logger.log('error', f'Erro ao executar mkvinfo em {video_file}: {result.stderr}')
                return []
            self.logger.log('debug', f'Output mkvinfo (primeiras linhas): {result.stdout.split(chr(10))[:10]}')

            # Parse output to find subtitle tracks
            tracks = self.parse_mkv_tracks(result.stdout)
            self.logger.log('info', f'Encontradas {len(tracks)} tracks totais em {video_file}')
            subtitle_tracks = [t for t in tracks if t['type'] == 'subtitles']
            self.logger.log('info', f'Encontradas {len(subtitle_tracks)} tracks de legenda')

            # Selecionar track: manual (preferred_track_id) ou automático
            if preferred_track_id is not None:
                selected = [t for t in subtitle_tracks if t['id'] == preferred_track_id]
                if not selected:
                    self.logger.log('warning', f'Track {preferred_track_id} não encontrada; usando seleção automática')
                    selected = self.filter_subtitles(subtitle_tracks)
                else:
                    codec = selected[0].get('codec_id', '')
                    if codec == 'S_HDMV/PGS':
                        self.logger.log('warning', f'Track {preferred_track_id} é PGS (Blu-ray). OCR necessário para tradução.')
                valid_subs = selected
            else:
                valid_subs = self.filter_subtitles(subtitle_tracks)

            extracted = []

            # Handle PGS tracks via OCR fallback
            pgs_in_selection = [s for s in valid_subs if s.get('codec_id', '') == 'S_HDMV/PGS']
            text_subs = [s for s in valid_subs if s.get('codec_id', '') != 'S_HDMV/PGS']

            for sub in pgs_in_selection:
                ocr_result = self._try_ocr_pgs(video_file, sub)
                if ocr_result:
                    extracted.append(ocr_result)

            for sub in text_subs:
                # Determinar extensão baseada no codec
                codec = sub.get('codec_id', '')
                if codec == 'S_TEXT/ASS':
                    output_file = video_file.with_suffix(f'.track{sub["id"]}.ass')
                else:
                    output_file = video_file.with_suffix(f'.track{sub["id"]}.srt')

                # Check if already extracted
                if output_file.exists() and output_file.stat().st_size > 50:
                    self.logger.log('info', f'Track {sub["id"]} já extraída: {output_file}')
                    extracted.append((output_file, sub))
                    continue

                cmd = [self.mkvextract_path, 'tracks', str(video_file), f'{sub["id"]}:{output_file}']
                self.logger.log('debug', f'Executando: {" ".join(cmd)}')
                result = subprocess.run(cmd, capture_output=True, text=True)

                if result.returncode == 0:
                    self.fix_encoding(output_file)
                    if self.validate_subtitle_quality(output_file):
                        extracted.append((output_file, sub))
                    else:
                        self.logger.log('warning', f'Track {sub["id"]} tem qualidade baixa, ignorando')
                        if output_file.exists():
                            output_file.unlink()
                else:
                    self.logger.log('warning', f'Falha ao extrair track {sub["id"]} ({codec}): {result.stderr}')

            # If nothing extracted and there are PGS-only tracks, attempt OCR fallback
            if not extracted:
                all_pgs = [t for t in subtitle_tracks if t.get('codec_id', '') == 'S_HDMV/PGS']
                if all_pgs:
                    self.logger.log('info', 'Tentando OCR nas tracks PGS como fallback...')
                    for sub in all_pgs[:1]:  # Try first PGS track only
                        ocr_result = self._try_ocr_pgs(video_file, sub)
                        if ocr_result:
                            extracted.append(ocr_result)
                            break

            return extracted
        except Exception as e:
            self.logger.log('error', f'Erro ao extrair legendas de {video_file}: {str(e)}')
            return []

    def _try_ocr_pgs(self, video_file, sub: dict):
        """Extract a PGS track via mkvextract and run OCR on it.

        Returns (srt_path, sub_info) on success, None on failure.
        """
        try:
            from .ocr_extractor import PGSOCRExtractor, is_ocr_available
        except ImportError:
            self.logger.log('warning', 'ocr_extractor module not found')
            return None

        if not is_ocr_available():
            self.logger.log('warning', 'OCR não disponível (pytesseract/Tesseract não instalado). Instale via requirements-full.txt')
            return None

        track_id = sub['id']
        sup_file = video_file.with_suffix(f'.track{track_id}.sup')
        srt_file = video_file.with_suffix(f'.track{track_id}.ocr.srt')

        if srt_file.exists() and srt_file.stat().st_size > 50:
            self.logger.log('info', f'OCR SRT já existe: {srt_file}')
            return (srt_file, sub)

        # Extract .sup
        if not sup_file.exists():
            cmd = [self.mkvextract_path, 'tracks', str(video_file), f'{track_id}:{sup_file}']
            self.logger.log('info', f'Extraindo PGS track {track_id} para OCR...')
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0 or not sup_file.exists():
                self.logger.log('warning', f'Falha ao extrair track PGS {track_id}: {result.stderr}')
                return None

        # Determine Tesseract language from track language
        lang_map = {
            'eng': 'eng', 'jpn': 'jpn', 'spa': 'spa', 'fra': 'fra',
            'deu': 'deu', 'ita': 'ita', 'por': 'por', 'kor': 'kor',
            'chi': 'chi_sim',
        }
        track_lang = sub.get('language', 'und')
        tess_lang = lang_map.get(track_lang, 'eng')

        ocr = PGSOCRExtractor(logger=self.logger, tesseract_lang=tess_lang)
        if not ocr.check_tesseract_lang(tess_lang):
            self.logger.log('warning', f'Pacote de idioma Tesseract "{tess_lang}" não instalado. Usando "eng".')
            tess_lang = 'eng'
            ocr = PGSOCRExtractor(logger=self.logger, tesseract_lang=tess_lang)

        self.logger.log('info', f'Iniciando OCR na track PGS {track_id} (lang={tess_lang})...')
        success = ocr.extract_pgs_to_srt(str(sup_file), str(srt_file))

        # Clean up .sup file to save space
        try:
            sup_file.unlink()
        except OSError:
            pass

        if success and srt_file.exists() and srt_file.stat().st_size > 50:
            self.logger.log('info', f'OCR concluído: {srt_file}')
            return (srt_file, sub)

        self.logger.log('warning', f'OCR falhou para track {track_id}')
        return None

    def parse_mkv_tracks_from_file(self, video_path: str) -> list:
        """Run mkvinfo on a file and return parsed subtitle tracks. Used by the GUI for lazy loading."""
        if not self.mkvinfo_available:
            return []
        try:
            result = subprocess.run(
                [self.mkvinfo_path, '--ui-language', 'en', str(video_path)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                return []
            all_tracks = self.parse_mkv_tracks(result.stdout)
            return [t for t in all_tracks if t.get('type') == 'subtitles']
        except Exception:
            return []

    def parse_mkv_tracks(self, output):
        # Parser para mkvinfo output (formato árvore)
        tracks = []
        lines = output.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith('| + Track'):
                # Encontrou uma track, coletar informações
                track = {}
                i += 1
                while i < len(lines) and lines[i].strip().startswith('|  +'):
                    subline = lines[i].strip()[4:]  # Remove '|  +'
                    if ':' in subline:
                        key, value = subline.split(':', 1)
                        key = key.strip().lower().replace(' ', '_')
                        value = value.strip()
                        if key == 'track_number':
                            # Handle "4 (track ID for mkvmerge & mkvextract: 3)"
                            if '(' in value and 'mkvextract' in value:
                                # Extract the actual mkvextract ID
                                try:
                                    extract_id = value.split('mkvextract:')[1].split(')')[0].strip()
                                    track['extract_id'] = int(extract_id)
                                except:
                                    pass
                            try:
                                track['track_number'] = int(value.split()[0])
                            except:
                                track['track_number'] = 0
                        track[key] = value
                    i += 1
                # Verificar se é subtitle
                if track.get('track_type') == 'subtitles':
                    # Use extract_id if available, otherwise use track_number - 1
                    track_number = track.get('track_number', 1)
                    extract_id = track.get('extract_id')
                    
                    if extract_id is not None:
                        final_id = extract_id
                    else:
                        final_id = max(0, track_number - 1)  # Ensure non-negative
                    
                    tracks.append({
                        'id': final_id,
                        'track_number': track_number,
                        'type': 'subtitles',
                        'language': track.get('language', 'und'),
                        'name': track.get('name', ''),
                        'codec_id': track.get('codec_id', ''),
                        'default': track.get('default_track', 'no') == 'yes',
                        'forced': track.get('forced_track', 'no') == 'yes'
                    })
                    self.logger.log('debug', f'Track encontrada: número={track_number}, extract_id={final_id}, codec={track.get("codec_id")}')
            else:
                i += 1
        return tracks

    def filter_subtitles(self, tracks):
        # Log all available tracks for debugging
        self.logger.log('debug', 'Tracks disponíveis:')
        for track in tracks:
            name = track.get('name', '').lower()
            codec = track.get('codec_id', 'unknown')
            self.logger.log('debug', f"  Track {track['id']} (número {track.get('track_number', '?')}): {codec} - {track.get('language', 'und')} - Name: '{track.get('name', '')}' - Forced: {track.get('forced', False)}")
        
        # Separate PGS from text-based tracks
        non_pgs_tracks = [t for t in tracks if t.get('codec_id', '') != 'S_HDMV/PGS']
        pgs_tracks = [t for t in tracks if t.get('codec_id', '') == 'S_HDMV/PGS']

        if not non_pgs_tracks:
            if pgs_tracks:
                self.logger.log('warning', 'Apenas legendas PGS/Blu-ray encontradas. OCR necessário.')
            else:
                self.logger.log('warning', 'Nenhuma track de legenda encontrada.')
            return []
        
        # Priorizar tracks de diálogo sobre signs/songs
        dialogue_tracks = []
        other_tracks = []
        
        for track in non_pgs_tracks:
            name = track.get('name', '').lower()
            # Identificar tracks de diálogo
            if any(keyword in name for keyword in ['dialogue', 'dialog', 'full', 'complete']):
                dialogue_tracks.append(track)
            # Evitar tracks de signs/songs
            elif not any(keyword in name for keyword in ['sign', 'song', 'opening', 'ending', 'op', 'ed']):
                other_tracks.append(track)
        
        # Priorizar tracks de diálogo
        priority_tracks = dialogue_tracks if dialogue_tracks else other_tracks
        
        # Filtrar tracks não forçadas
        valid_tracks = [t for t in priority_tracks if not t.get('forced', False)]
        if not valid_tracks:
            valid_tracks = priority_tracks  # Fallback para tracks forçadas se necessário
        
        # Priorizar inglês
        english_tracks = [t for t in valid_tracks if t.get('language') == 'eng']
        if english_tracks:
            selected = english_tracks[0]
            self.logger.log('info', f'Selecionada track inglesa: {selected["id"]} - {selected.get("name", "")} ({selected.get("codec_id", "unknown")})')
            return [selected]
        
        # Se não tem inglês, pegar qualquer track válida
        if valid_tracks:
            selected = valid_tracks[0]
            self.logger.log('info', f'Selecionada track: {selected["id"]} - {selected.get("name", "")} ({selected.get("codec_id", "unknown")})')
            return [selected]
        
        self.logger.log('warning', 'Nenhuma track de legenda válida encontrada')
        return []

    def extract_other_subtitles(self, video_file):
        if not self.ffmpeg_available:
            self.logger.log('warning', f'ffmpeg não disponível para extrair legendas de {video_file}')
            return []
        self.logger.log('info', f'Tentando extrair legenda com ffmpeg de {video_file}')
        # Tentar extrair primeira track de legenda com ffmpeg
        try:
            output_file = video_file.with_suffix('.srt')
            
            # Check if already extracted
            if output_file.exists() and output_file.stat().st_size > 50:
                self.logger.log('info', f'Legenda já extraída: {output_file}')
                return [(output_file, {'language': 'und'})]
            
            cmd = [self.ffmpeg_path, '-i', str(video_file), '-map', '0:s:0', '-c:s', 'text', '-f', 'srt', str(output_file)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0 and output_file.exists():
                self.fix_encoding(output_file)
                self.logger.log('info', f'Legenda extraída com ffmpeg: {output_file}')
                return [(output_file, {'language': 'und'})]  # Assumir idioma desconhecido
            else:
                self.logger.log('warning', f'ffmpeg falhou para {video_file}: {result.stderr}')
                return []
        except Exception as e:
            self.logger.log('error', f'Erro ao extrair com ffmpeg: {str(e)}')
            return []

    def validate_subtitle_quality(self, subtitle_file):
        """Validate if subtitle has useful content"""
        try:
            if not subtitle_file.exists() or subtitle_file.stat().st_size < 100:
                return False
                
            with open(subtitle_file, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            if not content.strip():
                return False
            
            # Remove timestamps and formatting
            import re
            clean_text = re.sub(r'\d+:\d+:\d+[,.]\d+ --> \d+:\d+:\d+[,.]\d+', '', content)
            clean_text = re.sub(r'^\d+$', '', clean_text, flags=re.MULTILINE)
            clean_text = re.sub(r'\{[^}]*\}', '', clean_text)  # Remove ASS tags
            clean_text = re.sub(r'<[^>]*>', '', clean_text)    # Remove HTML tags
            clean_text = re.sub(r'\[.*?\]', '', clean_text)    # Remove [tags]
            
            # Count actual text content
            text_lines = [line.strip() for line in clean_text.split('\n') if line.strip()]
            
            # Quality checks
            if len(text_lines) < 3:  # Too few lines (reduced from 5)
                return False
            
            # Check for meaningful content (not just symbols/numbers)
            meaningful_lines = 0
            total_chars = 0
            for line in text_lines:
                if len(line) > 2 and any(c.isalpha() for c in line):
                    meaningful_lines += 1
                    total_chars += len(line)
            
            # At least 20% should be meaningful text (reduced from 30%)
            if len(text_lines) > 0 and meaningful_lines / len(text_lines) < 0.2:
                return False
            
            # Check average line length (should have some substance)
            if meaningful_lines > 0 and total_chars / meaningful_lines < 5:
                return False
            
            return True
            
        except Exception:
            return False
    
    def fix_encoding(self, file_path):
        with open(file_path, 'rb') as f:
            raw = f.read()
        detected = chardet.detect(raw)
        if detected['encoding'] not in ['utf-8', None]:
            text = raw.decode(detected['encoding'])
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(text)