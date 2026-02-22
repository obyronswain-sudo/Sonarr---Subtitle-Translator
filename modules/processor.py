"""
Video Processor Module
Handles video file processing for subtitle translation
"""
from pathlib import Path
from typing import Dict, List, Any
import re

class SubtitleEntry:
    """Represents a single subtitle entry"""
    def __init__(self, index: int, start_time: float, end_time: float, text: str):
        self.index = index
        self.start_time = start_time
        self.end_time = end_time
        self.text = text
    
    def format_time(self, seconds: float) -> str:
        """Format seconds to SRT time format"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"
    
    def to_srt(self) -> str:
        """Convert to SRT format"""
        start = self.format_time(self.start_time)
        end = self.format_time(self.end_time)
        return f"{self.index}\n{start} --> {end}\n{self.text}\n"


class VideoProcessor:
    """
    Video processor for handling subtitle translation workflow
    Supports multiple initialization signatures for backward compatibility
    """
    
    def __init__(self,
                 base_dir: str = None,
                 keys: Dict[str, str] = None,
                 logger=None,
                 progress_callback=None,
                 stop_check=None,
                 api_type: str = 'Ollama',
                 translation_callback=None,
                 specific_files=None,
                 config: Dict[str, Any] = None,
                 series_metadata: Dict[str, Any] = None,
                 track_map: Dict[str, int] = None):
        # Assinaturas suportadas:
        # VideoProcessor(base_dir, keys, logger, api_type=...) - webhook
        # VideoProcessor(base_dir, keys, logger, progress_callback, stop_check, api_type, translation_callback, specific_files=...) - GUI
        self.logger = logger
        self.config = keys if keys is not None else (config or {})
        self.base_dir = Path(base_dir) if base_dir else None
        self.api_type = api_type
        self.progress_callback = progress_callback
        self.stop_check = stop_check
        self.translation_callback = translation_callback
        self.specific_files = specific_files
        self.series_metadata = series_metadata
        self.track_map = track_map or {}
        self.skip_existing = self.config.get('skip_existing', True)
        self.supported_formats = ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.srt', '.ass', '.vtt']
        self.stop_flag = False
        
    def is_video_file(self, file_path: str) -> bool:
        """Check if file is a video file"""
        ext = Path(file_path).suffix.lower()
        return ext in self.supported_formats
    
    def is_subtitle_file(self, file_path: str) -> bool:
        """Check if file is a subtitle file"""
        ext = Path(file_path).suffix.lower()
        return ext in ['.srt', '.ass', '.vtt']
    
    def get_video_info(self, file_path: str) -> Dict[str, Any]:
        """Get information about a video file"""
        path = Path(file_path)
        return {
            'path': str(path.absolute()),
            'filename': path.name,
            'extension': path.suffix.lower(),
            'size': path.stat().st_size if path.exists() else 0,
            'exists': path.exists()
        }
    
    def find_subtitle_files(self, video_path: str) -> List[str]:
        """Find subtitle files for a video"""
        video_path = Path(video_path)
        subtitles = []
        
        for ext in ['.srt', '.ass', '.vtt']:
            sub_path = video_path.with_suffix(ext)
            if sub_path.exists():
                subtitles.append(str(sub_path))
        
        return subtitles
    
    def scan_directory(self, directory: str, recursive: bool = True) -> List[Dict[str, Any]]:
        """Scan directory for video files"""
        results = []
        base_path = Path(directory)
        
        if recursive:
            pattern = '**/*'
        else:
            pattern = '*'
            
        for file_path in base_path.glob(pattern):
            if file_path.is_file() and self.is_video_file(str(file_path)):
                video_info = self.get_video_info(str(file_path))
                video_info['subtitles'] = self.find_subtitle_files(str(file_path))
                results.append(video_info)
        
        if self.logger:
            self.logger.log('info', f'Scanned {directory}: Found {len(results)} video files')
        
        return results
    
    def parse_srt_timestamp(self, timestamp: str) -> float:
        """Parse SRT timestamp to seconds"""
        # Format: 00:01:23,456
        try:
            parts = timestamp.replace(',', '.').split(':')
            hours = float(parts[0])
            minutes = float(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
        except (ValueError, IndexError):
            return 0.0
    
    def parse_srt(self, content: str) -> List[SubtitleEntry]:
        """Parse SRT content into subtitle entries"""
        entries = []
        blocks = content.strip().split('\n\n')
        
        for block in blocks:
            lines = block.split('\n')
            if len(lines) >= 3:
                try:
                    index = int(lines[0])
                    times = lines[1].split(' --> ')
                    start = self.parse_srt_timestamp(times[0])
                    end = self.parse_srt_timestamp(times[1])
                    text = '\n'.join(lines[2:])
                    entries.append(SubtitleEntry(index, start, end, text))
                except (ValueError, IndexError):
                    continue
        
        return entries
    
    def parse_ass_timestamp(self, timestamp: str) -> float:
        """Parse ASS timestamp to seconds"""
        # Format: 0:00:01.23
        try:
            parts = timestamp.split(':')
            hours = float(parts[0])
            minutes = float(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
        except (ValueError, IndexError):
            return 0.0
    
    def format_ass_timestamp(self, seconds: float) -> str:
        """Format seconds to ASS timestamp"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        cs = int((seconds % 1) * 100)
        return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"
    
    def parse_ass(self, content: str) -> List[Dict[str, Any]]:
        """Parse ASS subtitle content"""
        events = []
        in_events = False
        
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('[Events]'):
                in_events = True
                continue
            if line.startswith('['):
                in_events = False
                continue
            if in_events and line.startswith('Dialogue:'):
                parts = line[9:].split(',', 9)
                if len(parts) >= 10:
                    try:
                        start = self.parse_ass_timestamp(parts[1])
                        end = self.parse_ass_timestamp(parts[2])
                        text = parts[9]
                        events.append({
                            'start': start,
                            'end': end,
                            'text': text,
                            'style': parts[3],
                            'actor': parts[4]
                        })
                    except (ValueError, IndexError):
                        continue
        
        return events
    
    def _rebuild_subtitle(self, format_type: str, original: List, translated: List) -> str:
        """Rebuild subtitle with translated text"""
        if format_type == '.srt':
            result = []
            for i, entry in enumerate(original):
                trans_text = translated[i].text if i < len(translated) else entry.text
                result.append(f"{entry.index}")
                result.append(f"{entry.format_time(entry.start_time)} --> {entry.format_time(entry.end_time)}")
                result.append(trans_text)
                result.append('')
            return '\n'.join(result)
        elif format_type == '.ass':
            lines = ['[Script Info]', 'ScriptType: v4.00+', '[V4+ Styles]', 
                     'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
                     'Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1',
                     '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
            
            for i, entry in enumerate(original):
                trans_text = translated[i]['text'] if i < len(translated) else entry['text']
                start = self.format_ass_timestamp(entry['start'])
                end = self.format_ass_timestamp(entry['end'])
                lines.append(f"Dialogue: 0,{start},{end},{entry.get('style', 'Default')},,0,0,0,,{trans_text}")
            
            return '\n'.join(lines)
        return ''
    
    def process_all(self, specific_files=None):
        """Process all subtitle files: extract from video (mkvextract) if needed, translate, preserve timings, create .ass/.srt, remove base file."""
        from .translator import Translator
        from .file_utils import safe_read_subtitle
        from .extractor import SubtitleExtractor
        
        if not self.base_dir:
            if self.logger:
                self.logger.log('warning', 'No base directory set for processing')
            return
        
        if self.logger:
            self.logger.log('info', f'Starting processing in: {self.base_dir}')
        
        # Initialize translator and extractor (mkvextract for MKV)
        self.translator = Translator(
            keys=self.config,
            logger=self.logger,
            api_type=self.api_type,
            translation_callback=getattr(self, 'translation_callback', None),
            max_parallelism=self.config.get('parallelism', 1)
        )
        translator = self.translator
        extractor = SubtitleExtractor(self.logger)
        
        # Find subtitle files to process
        subtitle_files = []
        files_param = specific_files if specific_files is not None else self.specific_files
        
        if files_param:
            # Arquivos vindos da GUI/Sonarr: podem ser vídeos (.mkv etc.) ou legendas já existentes
            for file_path in files_param:
                path = Path(file_path)
                if not path.exists():
                    continue
                if self.is_video_file(str(path)):
                    # Extrair track de legenda correta com mkvextract (MKV) ou ffmpeg
                    preferred_tid = self.track_map.get(str(path))
                    extracted = extractor.extract_subtitles(path, preferred_track_id=preferred_tid)
                    for sub_path, _info in (extracted or []):
                        sub_path = Path(sub_path)
                        if sub_path not in subtitle_files and self.is_subtitle_file(str(sub_path)):
                            subtitle_files.append(sub_path)
                elif self.is_subtitle_file(str(path)):
                    if path not in subtitle_files:
                        subtitle_files.append(path)
        else:
            # Varredura no diretório: legendas já no disco
            target_lang_lower = self.config.get('target_lang', 'pt-BR').lower()
            for ext in ['.srt', '.ass', '.vtt']:
                for p in self.base_dir.glob(f'**/*{ext}'):
                    if p.is_file() and p not in subtitle_files:
                        name_lower = p.name.lower()
                        if f'.{target_lang_lower}.' in name_lower or name_lower.endswith(f'.{target_lang_lower}' + ext):
                            continue
                        subtitle_files.append(p)
        
        if not subtitle_files:
            if self.logger:
                self.logger.log('warning', f'No subtitle files found in {self.base_dir}')
            return
        
        if self.logger:
            self.logger.log('info', f'Found {len(subtitle_files)} subtitle files to process')
        
        # Process each subtitle file
        total_files = len(subtitle_files)
        for idx, subtitle_path in enumerate(subtitle_files):
            if self.stop_flag or (self.stop_check and self.stop_check()):
                if self.logger:
                    self.logger.log('info', 'Processing stopped by user')
                break
            
            try:
                if self.logger:
                    self.logger.log('info', f'Processing: {subtitle_path.name}')
                
                # Check if already translated
                target_lang = self.config.get('target_lang', 'pt-BR')
                output_file = subtitle_path.with_name(subtitle_path.stem + f'.{target_lang}' + subtitle_path.suffix)
                if output_file.exists() and self.skip_existing:
                    if self.logger:
                        self.logger.log('info', f'Already translated: {output_file.name}')
                    continue
                
                # Read subtitle file
                content, encoding = safe_read_subtitle(subtitle_path)
                if not content:
                    if self.logger:
                        self.logger.log('warning', f'Could not read: {subtitle_path.name}')
                    continue
                
                # Traduzir via translate_subtitle (usa PromptBuilder, LineClassifier, etc.)
                result_path = translator.translate_subtitle(
                    subtitle_path,
                    target_lang=target_lang,
                    series_metadata=self.series_metadata,
                )
                if result_path:
                    if self.logger:
                        self.logger.log('info', f'✓ Saved: {result_path.name}')
                    # If review callback is set, emit the translated content for review
                    review_cb = getattr(self, 'review_callback', None)
                    if review_cb and result_path.exists():
                        try:
                            review_lines = self._extract_review_lines(subtitle_path, result_path)
                            if review_lines:
                                review_cb(str(result_path), review_lines)
                        except Exception as _re:
                            if self.logger:
                                self.logger.log('warning', f'Review extraction failed: {_re}')
                    try:
                        subtitle_path.unlink()
                        if self.logger:
                            self.logger.log('info', f'Removed base file: {subtitle_path.name}')
                    except OSError as e:
                        if self.logger:
                            self.logger.log('warning', f'Could not remove base file {subtitle_path.name}: {e}')
                else:
                    if self.logger:
                        self.logger.log('warning', f'translate_subtitle returned None for {subtitle_path.name}, skipping')
                
                # Update progress
                progress = ((idx + 1) / total_files) * 100
                if hasattr(self, 'progress_callback') and self.progress_callback:
                    self.progress_callback(progress)
                    
            except Exception as e:
                if self.logger:
                    self.logger.log('error', f'Error processing {subtitle_path.name}: {e}')
                continue
        
        if self.logger:
            self.logger.log('info', f'Processing completed! Processed {total_files} files')
    
    def _extract_review_lines(self, original_path: Path, translated_path: Path) -> list:
        """Return list of (original_line, translated_line) pairs for the review dialog."""
        import re

        def _srt_lines(path):
            try:
                raw, _ = safe_read_subtitle(path)
                if not raw:
                    return []
                blocks = re.split(r'\n\s*\n', raw.strip())
                lines = []
                for block in blocks:
                    parts = block.strip().split('\n')
                    if len(parts) >= 3:
                        lines.append('\n'.join(parts[2:]))
                return lines
            except Exception:
                return []

        orig_lines = _srt_lines(original_path)
        trans_lines = _srt_lines(translated_path)
        if not orig_lines or not trans_lines:
            return []
        pairs = list(zip(orig_lines, trans_lines))
        return pairs

    def stop(self):
        """Stop processing and propagate to translator"""
        self.stop_flag = True
        if hasattr(self, 'translator') and self.translator:
            self.translator.stop_processing = True
        if self.logger:
            self.logger.log('info', 'Processing stopped by user')

    def stop_processing(self):
        """Alias for stop() - used by GUI"""
        self.stop()

