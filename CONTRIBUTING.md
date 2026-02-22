# Contribuindo com o Tradutor de Legendas com IA

Obrigado pelo interesse em contribuir! Este guia explica como configurar o ambiente de desenvolvimento e a estrutura do projeto.

---

## Configuração do ambiente

### 1. Pré-requisitos

- Python 3.10 ou superior
- [Ollama](https://ollama.ai) (para testar tradução local)
- [MKVToolNix](https://www.mkvtoolnix.download/) (para testar extração de legendas MKV)
- [ffmpeg](https://ffmpeg.org/download.html) (para outros formatos de vídeo)
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) (apenas para testar OCR de PGS/VobSub)

### 2. Clone e configure

```bash
git clone https://github.com/seu-usuario/seu-repositorio.git
cd seu-repositorio

# Crie e ative um ambiente virtual
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

# Instale as dependências
pip install -r requirements-full.txt
```

### 3. Configure o projeto

```bash
cp config.example.json config.json
```

Edite `config.json` com suas configurações locais. Esse arquivo está no `.gitignore` e **nunca deve ser commitado**.

### 4. Execute

```bash
python main.py
```

---

## Estrutura do projeto

```
.
├── main.py                        # Ponto de entrada
├── config.example.json            # Template de configuração (sem segredos)
├── requirements.txt               # Dependências essenciais
├── requirements-full.txt          # Todas as dependências (incluindo opcionais)
├── LICENSE
├── README.md
├── CONTRIBUTING.md
└── modules/
    ├── gui_sonarr.py              # Interface gráfica principal (PySide6)
    ├── episode_dialog.py          # Diálogo de seleção de episódios e tracks
    ├── scanner.py                 # Varredura recursiva de arquivos de vídeo
    ├── extractor.py               # Extração de legendas (MKV, ffmpeg, OCR)
    ├── ocr_extractor.py           # OCR para PGS/VobSub (Tesseract + OpenCV)
    ├── translator.py              # Motor de tradução (Ollama, DeepL, GPT, Gemini, etc.)
    ├── prompt_builder.py          # Construção dinâmica de prompts por idioma e API
    ├── processor.py               # Orquestração do fluxo de processamento
    ├── translation_cache.py       # Cache híbrido (LRU em memória + SQLite persistente)
    ├── webhook_server.py          # Servidor Flask para integração com Sonarr
    ├── hardware_detector.py       # Detecção de RAM/VRAM/CPU
    ├── dependency_installer.py    # Instalação automática de dependências opcionais
    ├── logger.py                  # Sistema de logs
    ├── config_loader.py           # Carregamento de config.json e variáveis .env
    └── ollama_client.py           # Cliente Ollama com gerenciamento de modelos
```

---

## Fluxo principal de processamento

```
GUI (gui_sonarr.py)
  └─► ProcessingWorker (QThread)
        └─► VideoProcessor (processor.py)
              ├─► SubtitleExtractor (extractor.py)
              │     └─► PGSOCRExtractor (ocr_extractor.py)  ← se PGS/VobSub
              └─► Translator (translator.py)
                    ├─► PromptBuilder (prompt_builder.py)
                    └─► TranslationCache (translation_cache.py)
```

---

## Convenções de código

- **Python 3.10+**: use type hints onde possível
- **PySide6**: toda interação com a UI deve acontecer na thread principal; use `QThread` + `Signal` para tarefas em background
- **Sem segredos no código**: chaves de API e configurações sensíveis ficam em `config.json` (ignorado pelo git)
- **Sem `except: pass` silencioso**: sempre logue ou trate erros de forma explícita
- **Subprocess**: sempre valide entradas do usuário antes de passar para `subprocess.run`

---

## Adicionando suporte a uma nova API de tradução

1. Importe a biblioteca no topo de `translator.py` com `try/except ImportError`
2. Inicialize o cliente em `_initialize_apis` dentro do `__init__`
3. Adicione a lógica de tradução no método `translate` (bloco `if api == 'SuaAPI'`)
4. Adicione o nome da API na lista de seleção em `gui_sonarr.py`
5. Adicione a dependência em `requirements-full.txt` e em `dependency_installer.py`
6. Atualize `prompt_builder.py` se a API precisar de um formato de prompt específico

---

## Reportando bugs

Abra uma [issue](../../issues) com:
- Versão do Python e sistema operacional
- Passos para reproduzir o problema
- Trecho relevante do `app.log` (sem chaves de API)
- Comportamento esperado vs. observado

---

## Pull Requests

1. Crie um branch a partir de `main`: `git checkout -b minha-feature`
2. Faça suas alterações e teste localmente
3. Certifique-se de que `config.json` **não** está incluído no commit
4. Abra um PR descrevendo o que foi alterado e por quê
