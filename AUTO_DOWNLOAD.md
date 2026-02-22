# Auto-Download de Modelos - Guia Rápido

## O que é?

O programa agora faz **download automático** de modelos Ollama se não encontrar o modelo selecionado.

## Quando acontece o download automático?

1. **Ao iniciar o programa**: Verifica se o modelo está disponível
2. **Ao processar legendas**: Antes de traduzir, confirma que o modelo existe
3. **Sem intervenção do usuário**: Tudo é automático

## Exemplo de uso

### Cenário 1: Primeiro uso
```
1. Abra o programa
2. Vá em Settings
3. Clique em [Recommend]
4. Aplicação recomenda: qwen2.5:14b
5. Log mostra: "📥 Iniciando download automático..."
6. Espera o download completar
7. Log mostra: "✅ Download concluído!"
8. Pronto para traduzir!
```

### Cenário 2: Processando legendas
```
1. Clique em processar série
2. Se modelo não estiver disponível:
   - Log: "⚠️ Modelo não encontrado"
   - Log: "📥 Iniciando download automático..."
   - Sistema baixa automaticamente
   - Log: "✅ Download concluído!"
   - Tradução continua
3. Fim!
```

## O que aparece no log

Você verá mensagens como:
```
[INFO] ⚠️ Modelo qwen2.5:14b-instruct-q4_K_M não encontrado em Ollama
[INFO] 📥 Iniciando download automático do modelo qwen2.5:14b-instruct-q4_K_M...
[INFO] 📥 pulling, pulling completion: 0%
[INFO] 📥 pulling, pulling completion: 25%
[INFO] 📥 pulling, pulling completion: 50%
[INFO] 📥 pulling, pulling completion: 75%
[INFO] ✅ Download concluído: qwen2.5:14b-instruct-q4_K_M
[INFO] ✅ Ollama conectado com modelo qwen2.5:14b-instruct-q4_K_M
```

## E se falhar?

Se o download falhar por algum motivo:
- Log mostra erro detalhado
- Sistema sugere tentar novamente depois
- Você pode fazer download manual: `ollama pull qwen2.5:14b-instruct-q4_K_M`

## Requisitos

- ✅ Ollama precisa estar rodando (`ollama serve`)
- ✅ Conexão com internet (para baixar modelo)
- ✅ Espaço em disco (modelos tem ~4-30GB)
- ✅ Tempo (primeiro download leva 10-30 minutos)

## FAQ

### P: Por que o download está lento?
R: Modelos grandes têm 4-30GB. Download depende de sua internet. 14B tem ~8GB.

### P: Posso cancelar o download?
R: Sim, clique no botão "Stop" durante o processamento.

### P: Pode fazer download de modelo diferente?
R: Sim! Mude o model em Settings e o programa vai fazer auto-download se necessário.

### P: O download é feito só uma vez?
R: Sim, depois que o modelo está baixado, o programa apenas verifica se existe.

### P: Posso desabilitar auto-download?
R: Não é possível desabilitar, mas você pode fazer download manual antes.

## Como saber se o download está funcionando?

1. Abra o programa
2. Vá em "Settings"
3. Clique em "[Recommend]"
4. Olhe o log area - deve mostrar progresso de download

## Modelos disponíveis

Você pode ver todos os modelos com:
```bash
ollama list
```

Ou instalar manualmente:
```bash
ollama pull qwen2.5:7b-instruct-q5_K_M      # Pequeno e rápido
ollama pull qwen2.5:14b-instruct-q4_K_M     # Equilibrado (recomendado)
ollama pull qwen2.5:32b-instruct-q4_K_M     # Grande e melhor qualidade
```

## Performance

- **Primeiro download**: 10-30 minutos (depende da internet)
- **Usos posteriores**: Modelo já está disponível, nenhum delay
- **Durante download**: Pode traduzir outros modelos (se tiver espaço)

## Próximas melhorias

Em futuras versões:
- [ ] Mostrar ETA de download
- [ ] Suporte para múltiplos modelos instalados
- [ ] Opção de pausar/retomar download
- [ ] Limpeza automática de modelos antigos

