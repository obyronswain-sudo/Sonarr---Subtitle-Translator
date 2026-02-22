#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Análise comparativa detalhada de traduções"""

import sys, os, re
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Ler arquivos
eng_file = r"subtitles\Psycho-Pass (2012) - S01E01 - Crime Coefficient [HDTV-1080p][AC3 2.0][x265]_track5_[eng].txt"
ptbr_file = r"subtitles\Psycho-Pass (2012) - S01E01 - Crime Coefficient [HDTV-1080p][AC3 2.0][x265].track4.pt-BR.txt"

def extract_dialogues(filename):
    """Extrai apenas linhas de diálogo do arquivo"""
    dialogues = []
    with open(filename, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('Dialogue:') and 'PP-Default' in line:
                # Extrair apenas o texto (após o último ,,)
                parts = line.split(',,')
                if len(parts) >= 2:
                    text = parts[-1].strip()
                    # Remover tags de formatação
                    text = re.sub(r'\{[^}]+\}', '', text)
                    text = re.sub(r'\\N', ' ', text)
                    if text:
                        dialogues.append(text)
    return dialogues

print("=" * 80)
print("ANÁLISE COMPARATIVA DE TRADUÇÕES")
print("=" * 80)

eng_lines = extract_dialogues(eng_file)
ptbr_lines = extract_dialogues(ptbr_file)

print(f"\n📊 Estatísticas:")
print(f"   Linhas em inglês: {len(eng_lines)}")
print(f"   Linhas em PT-BR: {len(ptbr_lines)}")

# Análise 1: Frases não traduzidas (idênticas)
print(f"\n{'='*80}")
print("1. FRASES NÃO TRADUZIDAS (idênticas ao original)")
print("="*80)

not_translated = []
for i, (eng, ptbr) in enumerate(zip(eng_lines, ptbr_lines)):
    if eng.strip().lower() == ptbr.strip().lower():
        not_translated.append((i+1, eng))

if not_translated:
    for line_num, text in not_translated[:20]:  # Mostrar primeiras 20
        print(f"   Linha {line_num}: '{text}'")
    if len(not_translated) > 20:
        print(f"   ... e mais {len(not_translated)-20} frases")
else:
    print("   ✓ Nenhuma frase idêntica encontrada!")

# Análise 2: Erros comuns de tradução
print(f"\n{'='*80}")
print("2. ERROS COMUNS DE TRADUÇÃO")
print("="*80)

common_errors = {
    'Tradução literal demais': [],
    'Falta de naturalidade': [],
    'Problemas de concordância': [],
    'Palavras em inglês misturadas': []
}

# Detectar palavras em inglês no meio do português
eng_word_pattern = re.compile(r'\b[A-Z][a-z]+\b')
for i, ptbr in enumerate(ptbr_lines):
    # Verificar se tem palavras inglesas no meio
    words = ptbr.split()
    for word in words:
        # Palavras que não devem estar em português (exceto nomes próprios conhecidos)
        if word in ['Are', 'You', 'Inspector', 'Our', 'target', 'is', 'repeat', 'Excuse', 'me']:
            common_errors['Palavras em inglês misturadas'].append((i+1, ptbr))
            break

# Análise 3: Qualidade da naturalidade
print(f"\n{'='*80}")
print("3. AVALIAÇÃO DE NATURALIDADE")
print("="*80)

# Padrões de tradução ruim
bad_patterns = [
    (r'\.{3,}', 'Excesso de pontos'),
    (r'\s{2,}', 'Espaços duplos'),
    (r'。', 'Pontuação japonesa'),
    (r'[\u4e00-\u9fff]', 'Caracteres chineses'),
]

issues = []
for i, ptbr in enumerate(ptbr_lines):
    for pattern, desc in bad_patterns:
        if re.search(pattern, ptbr):
            issues.append((i+1, desc, ptbr))

if issues:
    for line_num, issue_type, text in issues[:10]:
        print(f"   Linha {line_num} ({issue_type}): '{text[:60]}...'")
else:
    print("   ✓ Nenhum problema grave de formatação!")

# Análise 4: Exemplos de boa tradução
print(f"\n{'='*80}")
print("4. EXEMPLOS DE TRADUÇÃO BOA vs RUIM")
print("="*80)

sample_pairs = [
    ("Shit!", "Porra!" if "Porra" in str(ptbr_lines) else "Shit!"),
    ("Thank you", "Obrigado"),
    ("I repeat.", "Repito." if any("Repito" in line for line in ptbr_lines) else "I repeat."),
]

print("\n   Comparando alguns exemplos:")
for eng, expected_pt in sample_pairs:
    # Procurar no arquivo traduzido
    found = False
    for ptbr in ptbr_lines:
        if eng.lower() in eng_lines[ptbr_lines.index(ptbr) if ptbr in ptbr_lines else 0].lower():
            print(f"   EN: {eng}")
            print(f"   PT: {ptbr}")
            if ptbr == eng:
                print(f"   ❌ NÃO TRADUZIDO")
            else:
                print(f"   ✓ Traduzido")
            print()
            found = True
            break

print("=" * 80)
print("RESUMO DA ANÁLISE")
print("=" * 80)
print(f"Total de problemas encontrados: {len(not_translated) + len(issues)}")
print(f"  - Não traduzidas: {len(not_translated)}")
print(f"  - Problemas de formatação: {len(issues)}")
