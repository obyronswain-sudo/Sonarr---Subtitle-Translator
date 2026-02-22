# HOBBY - Tradutor de Legendas - Ollama

Esse é um programa a príncipio para ser utilizado com em conjunto com o Sonarr para traduzir legendas (em geral localmente), de animes. Pode ser aplicado para outros projetos mas não foi desenvolvido para isso. 

A principal diferença para o Lingarr é que você não precisa esperar o Bazarr achar a legenda para ele traduzir, mas sim, esse programa tem a capacidade de extrair direto do arquivo de vídeo, caso ele possua uma legenda dentro do arquivo .mkv .mp4 e etc... Mas você vai precisar escolher quais animes/séries traduzir. 

Aplicação 100% em Python com interface gráfica completa para extrair, traduzir e salvar legendas de animes e séries — com amplo suporte ao Sonarr, arquivos locais, múltiplos idiomas, OCR para Blu-ray e pré-visualização editável antes de salvar.

## Funcionalidades

### Fontes de conteúdo
- **Integração com Sonarr**: lista a biblioteca, seleciona séries/episódios e processa diretamente
- **Arquivos locais** (sem Sonarr): selecione uma pasta ou arquivos individuais pela aba *Local Files*
- **Webhook automático**: Sonarr notifica o app quando um episódio é baixado e a tradução inicia automaticamente

### Tradução
- **Ollama** (local, privado, gratuito) — recomendado
- **DeepL**, **GPT (OpenAI)**, **Gemini** — APIs externas com campo de chave na interface
- **Google Translate**, **LibreTranslate**, **Argos**, **CTranslate2** — alternativas gratuitas
- Idiomas de entrada e saída configuráveis (não limitado a inglês → português)
- Cache híbrido (memória + SQLite) para evitar retraduzir o mesmo texto
- Prompts otimizados por API: perfil enxuto para APIs pagas (menos tokens = menor custo)

### Extração e formatos
- Extrai tracks de legenda de `.mkv`, `.mp4`, `.avi`, `.mov`, `.wmv`
- Suporta `.srt`, `.ass`, `.ssa`, `.vtt`
- **OCR para Blu-ray**: converte legendas PGS (`.sup`) e VobSub (`.sub`/`.idx`) para texto via Tesseract + OpenCV
- Seleção manual ou automática da track de legenda por episódio

### Qualidade e usabilidade
- **Pré-visualização editável**: revise e edite a tradução antes de salvar o arquivo final
- **Notificação no sistema** ao concluir o processamento
- **Glossário**: termos globais e por série para consistência na tradução
- Validação de qualidade: detecta inversões semânticas, artefatos de LLM e proporções de comprimento
- Deduplicação inteligente e batching para reduzir chamadas à API
- Retry automático com backoff exponencial

---

## Requisitos

- Python 3.10+
- [Ollama](https://ollama.ai) — para tradução local (recomendado)
- [MKVToolNix](https://www.mkvtoolnix.download/) — para extração de legendas MKV
- [ffmpeg](https://ffmpeg.org/download.html) — para outros formatos de vídeo
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) — apenas para legendas PGS/VobSub (Blu-ray)

---

## Instalação

### 1. Clone o repositório

```bash
git clone https://github.com/obyronswain-sudo/Sonarr---Subtitle-Translator.git
cd Sonarr---Subtitle-Translator
```

### 2. Instale as dependências Python

Dependências essenciais:
```bash
pip install -r requirements.txt
```

Para usar APIs externas (DeepL, GPT, Gemini, Google Translate) ou OCR:
```bash
pip install -r requirements-full.txt
```

### 3. Configure o projeto

Copie o arquivo de exemplo e preencha com suas configurações:
```bash
cp config.example.json config.json
```

Edite `config.json`:
```json
{
  "sonarr_url": "http://localhost:8989",
  "sonarr_api_key": "SUA_CHAVE_SONARR",
  "ollama_url": "http://localhost:11434",
  "ollama_model": "qwen2.5:7b-instruct",
  "api": "Ollama",
  "source_lang": "auto",
  "target_lang": "pt-BR",
  "deepl_key": "",
  "gpt_key": "",
  "gemini_key": ""
}
```

> **Atenção:** `config.json` está no `.gitignore` e **nunca deve ser commitado**. Ele contém suas chaves de API.

### 4. Execute

```bash
python main.py
```

---

## Como usar

### Traduzir via Sonarr
1. Abra o app e vá em **Settings** → configure a URL e chave do Sonarr
2. Na aba **Series Library**, clique em **Refresh** para carregar sua biblioteca
3. Selecione uma série e clique em **Translate Episodes**
4. Escolha os episódios, a track de legenda (ou deixe em *Auto*) e confirme

### Traduzir arquivos locais
1. Vá na aba **Local Files**
2. Selecione **Folder** ou **Individual Files**
3. Clique em **Browse** para escolher o caminho
4. Clique em **Scan** e depois em **Process**

### Configurar idiomas
Em **Settings → Translation Engine**:
- **Source Language**: idioma original das legendas (ou *Auto* para detecção automática)
- **Target Language**: idioma de saída (ex: `pt-BR`, `es`, `fr`)

### Usar APIs externas
Em **Settings → External API Key**, insira a chave da API desejada. As chaves são salvas em `config.json` (que está no `.gitignore`).

---

## Estrutura dos módulos

| Arquivo | Função |
|---|---|
| `main.py` | Ponto de entrada |
| `modules/gui_sonarr.py` | Interface gráfica principal |
| `modules/episode_dialog.py` | Diálogo de seleção de episódios e tracks |
| `modules/scanner.py` | Varredura de arquivos de vídeo |
| `modules/extractor.py` | Extração de legendas (MKV, ffmpeg, OCR) |
| `modules/ocr_extractor.py` | OCR para legendas PGS/VobSub (Blu-ray) |
| `modules/translator.py` | Motor de tradução (todos os backends) |
| `modules/prompt_builder.py` | Construção dinâmica de prompts por idioma e API |
| `modules/processor.py` | Orquestração do fluxo de processamento |
| `modules/translation_cache.py` | Cache híbrido (memória + SQLite) |
| `modules/webhook_server.py` | Servidor Flask para integração com Sonarr |
| `modules/hardware_detector.py` | Detecção de RAM/VRAM/CPU para recomendação de modelo |
| `modules/dependency_installer.py` | Instalação automática de dependências opcionais |
| `modules/logger.py` | Sistema de logs |
| `modules/config_loader.py` | Carregamento de configurações e variáveis de ambiente |

---

## Segurança

- As chaves de API são armazenadas em `config.json`, que está no `.gitignore`
- O webhook Flask escuta apenas em `127.0.0.1` por padrão e exige um token de autenticação (`X-Sonarr-Token`)
- Caminhos recebidos pelo webhook são validados contra o diretório de mídia configurado
- Nomes de modelos Ollama são validados com regex antes de serem passados ao subprocess

---


##DISCLAIMER 

Essa é uma versão beta, mas funcional principalmente com o ollama, pode ser um pouco chatinho de configurar, por isso em breve deixarei essa versão compilada para facilitar. 

OUTRA COISA!!! A qualidade da tradução dependente exclusivamente do modelo utilizado, meu melhor resultado foi com o qwen 32b, porém demora bastante. Eu tive um resultado satisfátório também com o llama 3:8b, então pesquise e sinta-se livre para usar qual modelo achar melhor.

Esse programa foi feito por um leigo, pra suprir uma deficiência bem nichada e não precisar fazer tudo na mão com o OpenSubtitles com animes que não tem legenda em PT-BR em nenhum lugar, prestígie os trabalho dos tradutores, e para os casos onde não consiga encontrar nenhuma legenda no seu idioma de maneira nenhuma, esse programa tem o seu lugar ao sol. 

## Licença

[MIT](LICENSE)
