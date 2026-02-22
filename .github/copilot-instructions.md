# AI Coding Agent Instructions for Sonarr Subtitle Translator

## Project Overview
**SonarrSubtitleTranslator** is a production-grade Python/PySide6 application for automated subtitle translation and syncing. It integrates with Sonarr media servers, extracts subtitles from video files (MKV, MP4), detects language, translates to Portuguese (BR), and manages glossaries for anime terminology.

### Core Architecture
- **GUI**: PySide6 (`modules/gui_sonarr.py`, 2175 lines) - main window with episode browser, settings, real-time progress
- **Processing Pipeline**: `VideoProcessor` orchestrates extraction → detection → translation → writing
- **Translation Layer**: Multi-API support (DeepL, Ollama, GPT, Google, Gemini) with fallbacks and smart batching
- **Sonarr Integration**: REST API client syncs with media server, webhooks for event handling
- **Optimization Features**: Deduplication (~40-60% API savings), smart batching (5 lines/batch max), rate limiting, glossary context

### Directory Structure
- `modules/` - Core business logic (28 files)
  - `processor.py` - Main orchestrator, threading, quality checks
  - `translator.py` - Multi-API translation engine (2294 lines)
  - `extractor.py` - Subtitle extraction from video containers
  - `scanner.py`, `language_detector.py` - File discovery and language detection
  - `glossary_manager.py` - Anime terminology mapping (701 lines)
  - `sonarr_client.py` - Sonarr REST API integration
  - `gui_sonarr.py` - Main GUI entry point
  - `webhook_server.py` - Handles Sonarr webhooks
  - `*_client.py` - Ollama, CTranslate2, Argos offline translation clients
  - `translation_optimizer.py` - Dedup + smart batching logic
  - `translation_tracker.py` - Episode status caching (JSON index)
  - `quality_validator.py` - Output validation (line counts, timestamps)
- `config.json` - API keys, hardware settings, rate limits
- `translation_index.json` - Cache of translated episodes

## Critical Developer Workflows

### Running the Application
```bash
python main.py  # Launches PySide6 GUI
```
Or use the VS Code task: `Run Script` (runs `python main.py`)

### Adding Translation APIs
1. Add new translator class in `modules/` (e.g., `modules/myapi_client.py`)
2. Implement interface matching pattern in `translator.py` (handle rate limits, retries)
3. Register in `Translator.__init__()` conditional imports + setup
4. Add dropdown UI option in `gui_sonarr.py` settings tab
5. Test with `python test_translate_direct.py` (provides direct API testing)

### Modifying the GUI
- Entry point: `gui_sonarr.py` (2175 lines, monolithic - consider refactoring)
- Theme system: `modules/modern_theme.py` (PySide6 stylesheets)
- Dialogs: `modules/episode_dialog.py` (series/episode browser)
- Icons: `images/` directory (loaded via `get_icon_path()`)
- Status updates: Use `QThread` with signals for non-blocking operations

### Testing & Debugging
- Unit tests: `test_*.py` files in root (28+ test files)
- Quick validation: `python test_quality_validation.py`, `python test_imports.py`
- Direct API testing: `test_translate_direct.py`, `test_direct_ollama.py`
- Problem diagnosis: `test_problem_lines.py`, `debug_*.py` files
- **Note**: Tests rely on `config.json` having valid API keys for external services

## Project-Specific Conventions & Patterns

### Logging Pattern
```python
from modules.logger import Logger
logger = Logger()
logger.log('info', 'Message')
logger.log('warning', 'Message')
logger.log('error', 'Message')
```
Logs go to `subtitle_translator.log` + GUI display (real-time in log text area)

### Threading & Progress
- Main GUI logic runs in QThread to prevent freezing
- Use `progress_callback()` for percentage updates (0-100)
- Use `translation_progress_callback()` for per-batch translation updates
- Use queues (`extraction_queue`, `translation_queue`) for thread-safe work distribution
- **Key**: Always wrap file operations in try/except - missing mkvtoolnix/ffmpeg is common

### File Handling
- Use `pathlib.Path` exclusively (not string paths)
- Output subtitle format: `.pt-BR.srt` or `.pt-BR.ass` (language code in filename)
- Check existing files with `_check_file_quality()` before skipping (validates line counts)
- Handle race conditions: Use `_quality_cache_lock` when caching file checks

### Translation Optimization
**Critical for performance**: 
- `TranslationOptimizer.deduplicate_texts()` removes duplicate lines BEFORE API calls (massive savings)
- `SmartBatcher` splits episodes into 5-line batches (prevents timeout, improves quality)
- Parallel workers capped at 2 (GPU safety, prevents OOM)
- `TranslationCache` stores results in-memory per session
- **Glossary context**: `GlossaryManager.apply_glossary()` processes output, preserving capitalization

### Configuration Loading
```python
from modules.config_loader import ConfigLoader
config = ConfigLoader().load()
api_key = config.get('deepl_api_key')
model = config.get('ollama_model', 'mistral')
```
Config keys: `deepl_api_key`, `openai_api_key`, `ollama_host`, `ollama_model`, `parallelism`, `skip_existing`, etc.

### Episode Status Tracking
```python
tracker = TranslationTracker()  # Loads from translation_index.json
tracker.translated_episodes[series_id][episode_id] = {'status': 'translated', 'timestamp': ...}
tracker.save_index()  # Persists to disk
```
Prevents re-translating episodes (critical for Sonarr integration workflow)

### Quality Validation Pattern
```python
from modules.quality_validator import QualityValidator
validator = QualityValidator(logger)
if not validator.validate_subtitle_file(output_path, original_path):
    # Handle failure - likely missing ffmpeg/mkvtoolnix
    pass
```
Checks: line count parity, timestamp validity, special character handling

## Integration Points & External Dependencies

### External Tools (Must Be Installed)
- **mkvtoolnix** - Extracts subtitles from MKV containers (via `mkvextract`)
- **ffmpeg** - Extracts subtitles from MP4/other formats + OCR preprocessing
- **Tesseract** - OCR for VobSub (.sub) → text conversion
- **Ollama** - Local LLM for translation (runs on separate process/port, default 11434)

### API Integrations (Fallback Chain)
1. **DeepL** (primary, HTTPS) - Production-quality translation
2. **Ollama** (local HTTP) - Private, free, GPU-capable
3. **OpenAI GPT** - Expensive but highest quality
4. **Google Gemini** - Good quality, moderate cost
5. **Googletrans** - Free but slow, frequent blocks
6. **LibreTranslate** - Self-hosted option

All APIs wrapped in retry logic (`@retry` decorator) with exponential backoff.

### Sonarr REST API Flow
```
SonarrClient.get_series() → [Series metadata]
SonarrClient.get_episodes(series_id) → [Episodes]
SonarrClient.trigger_episode_refresh() → Forces Sonarr to detect new subtitles
```
Webhook server listens for episode/series add events → auto-queues for translation.

### Glossary System
- **Global glossary**: 400+ anime terms hardcoded in `GlossaryManager.GLOBAL_GLOSSARY`
- **Per-series glossaries**: Loaded from external JSON if available
- **Application**: Post-translation context replacement (preserves capitalization via regex)
- **Performance**: Glossary check disabled for non-anime content (language detection)

## Common Pitfalls & Solutions

### Missing External Tools
**Problem**: `extractor.py` fails silently if mkvtoolnix/ffmpeg not installed
**Solution**: Check `tool_detector.py`, add explicit error message to GUI startup
**Test**: `python test_imports.py` validates dependencies

### Ollama Connection Timeouts
**Problem**: Translation hangs if Ollama is slow to respond
**Solution**: Configure `ollama_timeout` in config (default 60s), add retry logic
**Current**: Auto-downloads models on startup (see `OPTIMIZATIONS.md`)

### Thread Safety in Translation Cache
**Problem**: Multiple worker threads writing to shared cache causes corruption
**Solution**: `TranslationCache` uses locks internally, but nested dictionary access needs review
**Current**: Safe for parallel=1-2, avoid increasing `max_parallelism` above 2

### GUI Freezing During File Operations
**Problem**: Large file I/O blocks main thread
**Solution**: All processing runs in `VideoProcessor` (queues + worker threads), GUI updates via signals
**Check**: Look for any direct Path operations in `gui_sonarr.py` main thread

### Subtitle File Format Detection
**Problem**: Program assumes output as SRT, but ASS format available
**Solution**: Use `file_utils.safe_write_subtitle()` - auto-detects + converts based on file extension
**Note**: Sonarr expects `.srt` by default, ASS used internally for special effects

## Code Modification Checklist

When making changes, verify:
- [ ] Threading: New file I/O in worker thread, not GUI thread
- [ ] Logging: All subprocess calls logged (mkvtoolnix, ffmpeg, ollama)
- [ ] Error handling: External tool failures handled gracefully with user message
- [ ] Configuration: New settings added to `config.json` schema + GUI settings tab
- [ ] Progress callbacks: Long operations report progress (0-100%)
- [ ] Testing: Run `python test_imports.py` + relevant `test_*.py` for new feature
- [ ] Quality validation: Confirm output subtitle has correct line counts (via `QualityValidator`)
- [ ] Glossary impact: If changing translation output, test glossary application still works

## Key Files by Purpose

| Purpose | Files |
|---------|-------|
| **Subtitle Extraction** | `extractor.py`, `tool_detector.py` |
| **API Translation** | `translator.py`, `*_client.py` modules |
| **Performance** | `translation_optimizer.py`, `translation_cache.py` |
| **GUI** | `gui_sonarr.py`, `modern_theme.py`, `episode_dialog.py` |
| **Sonarr Sync** | `sonarr_client.py`, `webhook_server.py`, `translation_tracker.py` |
| **Configuration** | `config_loader.py`, `config.json` |
| **Validation** | `quality_validator.py`, `language_detector.py` |
