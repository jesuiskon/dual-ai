# Passo zero: `/astra-review` em repositório de brinquedo

> **Registro histórico.** Este documento descreve decisões, hipóteses e verificações relatadas na data indicada. Não é um roteiro de instalação nem confirmação de que todas as tarefas foram concluídas. Consulte o [README atual](../../README.md) para começar e conhecer os limites da cópia preparada. Passos antigos de configuração não devem ser executados automaticamente.

Data: 2026-11-14. Máquina: desktop Windows 11. Nenhum dado real, nenhum arquivo de outro projeto privado.

## O que foi testado

Um repositório git de brinquedo (`textkit`) com um commit limpo (`slugify` e dois testes) e uma mudança não commitada com dois defeitos plantados:

| Defeito | Arquivo | O que está errado |
|---|---|---|
| D1 | `textkit/paging.py:3` | `paginate` usa `page * page_size`; a docstring diz que as páginas começam em 1, então a primeira página fica inacessível |
| D2 | `textkit/paging.py:9` | `page_count` usa divisão inteira e ignora a última página parcial |

Os testes que acompanhavam a mudança eram fracos de propósito e passavam com os dois defeitos (4 testes, OK).

Foi executado o fluxo do comando `/astra-review` do ECC 2.2.3, pelo script `scripts/astra-review.js`: prévia com `--dry-run`, depois a revisão real. A correção entre as rodadas foi feita pelo Claude desta sessão, sem subagente.

## Resultado

| | Rodada 1 | Rodada 2 (após a correção) |
|---|---|---|
| Veredito | FAIL, 2 achados HIGH | FAIL, 2 achados HIGH novos |
| Achados | D1 e D2, com cenário de falha e linha corretos | Falta de validação de argumentos inválidos (página negativa, `page_size` zero) |
| Duração | 26 s | 28 s |
| Tokens (entrada / em cache / saída) | 33.820 / 16.768 / 834 | 51.680 / 34.048 / 834 |

Entre as rodadas: 5 testes novos falharam antes da correção e os 9 testes passaram depois dela.

O experimento parou na rodada 2. A terceira rodada não foi executada.

## Respostas às quatro perguntas

1. **A integração funciona?** Sim. O Codex CLI 0.167.1, com o login ChatGPT já salvo, recebeu o diff, devolveu um veredito em JSON validado por schema e encontrou os dois defeitos plantados (2 de 2).
2. **Qual modelo?** `gpt-6-astra`, confirmado no log de sessão do Codex (`turn_context.model`).
3. **Quais permissões?** Aprovação `never` e sandbox `read-only`. Nenhuma permissão foi pedida a você. Busca na web desativada, servidores MCP desativados, configuração de usuário do Codex ignorada, e só variáveis do tipo PATH/HOME repassadas ao processo.
4. **Consumiu franquia?** Sim, da assinatura, e nada pago. O log registra plano `plus`, janela semanal, 2,0% de uso antes e depois das duas rodadas (o consumo ficou abaixo da resolução da leitura) e saldo de créditos zero, sem uso de créditos.

## O que o experimento ensinou

1. **O revisor não conseguiu ler arquivos nem rodar testes.** Com aprovação `never` neste Windows, o comando de shell do Codex foi rejeitado ("blocked by policy"). Ele revisou apenas com o diff recebido. Consequência: o pacote do revisor precisa ser autossuficiente.
2. **A primeira chamada do Codex foi gasta tentando carregar skills dele** (`~/.agents/skills/...`), o que foi bloqueado. Cerca de metade dos tokens de entrada da rodada 1 é custo fixo, não o diff de 30 linhas.
3. **A rodada 2 mudou o critério.** Na rodada 1 a falta de validação aparecia só na rubrica, sem achado bloqueante. Depois da correção, virou dois achados HIGH. Com a regra "repetir até passar", isso reproduz o ciclo de refinamento que queremos evitar.
4. **Autor sem ruído funciona.** Corrigir exatamente o que foi apontado, com teste que falha antes e passa depois, levou uma única edição.

## Limites deste teste

- Um único caso, pequeno, com defeitos fáceis. Não mede taxa de falso positivo nem defeitos sutis.
- Não foi testado o Codex como autor (sandbox de escrita) neste Windows.
- Não foi testado o `claude -p` como processo dirigido por script.
