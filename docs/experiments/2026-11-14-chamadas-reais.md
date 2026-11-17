# Chamadas reais do MVP: Codex, Claude e o modo `--restricted`

> **Registro histórico.** Este documento descreve decisões, hipóteses e verificações relatadas na data indicada. Não é um roteiro de instalação nem confirmação de que todas as tarefas foram concluídas. Consulte o [README atual](../../README.md) para começar e conhecer os limites da cópia preparada. Passos antigos de configuração não devem ser executados automaticamente.

Data: 2026-11-14. Windows 11, Claude Code 2.1.319, Codex CLI 0.167.1 (login ChatGPT, plano Plus).
Todas as chamadas foram feitas em repositórios de brinquedo descartáveis (`textkit`, o mesmo do [passo zero](2026-11-14-passo-zero-astra-review.md)). Nenhum dado real, nenhum arquivo privado.

Total: 4 chamadas ao Codex e 4 ao Claude.

## Verificações antes da primeira chamada

- Codex: `codex login status` respondeu "Logged in using ChatGPT".
- Nenhuma variável de chave de API (`ANTHROPIC_*`, `OPENAI_*`, `CODEX_*`) estava definida no ambiente.
- As opções de linha de comando usadas pelos adaptadores existem nas versões instaladas (conferido no texto de ajuda das duas CLIs).
- Os processos filhos recebem uma lista fechada de variáveis de ambiente. Variáveis de sessão, tokens e chaves de quem chama o `dualai` não são repassadas.

## Codex

| Chamada | Papel | Duração | Tokens de entrada | Tokens de saída | Resultado |
|---|---|---|---|---|---|
| "Responda apenas: ok" (amostra de saída) | nenhum | 6 s | 16.017 (9.984 em cache) | 5 | `ok` |
| Teste de fumaça do crítico | crítico | 9,7 s | 16.880 | 241 | 2 achados `relevante`: os dois defeitos plantados, com linha e cenário |
| Execução de ponta a ponta | crítico | 6,3 s | 19.038 | 39 | Nenhum achado |
| Teste de fumaça com resolução real | crítico | 8,4 s | 16.882 | 221 | 1 `critico` e 1 `relevante`: os dois defeitos plantados |

Configuração: aprovação `never`, sandbox `read-only`, configuração de usuário ignorada, busca na web desativada, prompt por stdin. O uso de tokens vem do evento `turn.completed` da saída JSONL.

Observações:

- Cerca de 16 mil tokens de entrada são custo fixo de cada chamada, mesmo para um prompt de três palavras.
- O crítico recebeu o pacote autossuficiente (tarefa, diff, arquivos alterados inteiros, saída dos testes) e respondeu em uma única chamada. No passo zero, com o `/astra-review`, a mesma revisão custou cerca de 34 mil tokens.
- Nos dois testes com defeitos plantados o crítico achou 2 de 2 e não produziu achados `opcional` ou `preferencia`.

## Claude: diagnóstico da configuração padrão

Primeira tentativa: `claude -p` com as configurações de usuário como estão (plugins e hook GateGuard ativos), ferramentas limitadas a leitura e edição de arquivos, teto de US$ 1,00.

| Medida | Valor |
|---|---|
| Duração | 23 s |
| Turnos | 3 |
| Tokens | 397.188 de criação de cache, 51.798 de leitura de cache, 562 de saída |
| Custo equivalente calculado pela CLI | US$ 3,20 |
| Modelo | `claude-opus-5-5` |
| Resultado | Parou em `error_max_budget_usd`. A primeira escrita de arquivo foi negada. Nenhum arquivo foi criado. |

Conclusão: nessa configuração cada chamada carrega todos os plugins e habilidades do usuário, o que custa centenas de milhares de tokens, e a primeira escrita é barrada. A CLI interrompeu a chamada por orçamento também com login de assinatura. Como o custo equivalente registrado excedeu o parâmetro configurado, esse ensaio não demonstra um teto financeiro rígido.

O que **não** foi tentado, por decisão do usuário de não desligar mecanismos de segurança: `disableAllHooks`, `ECC_GATEGUARD=off`, isenções de caminho no GateGuard. Nenhuma configuração do usuário foi alterada.

## Claude: modo `--restricted`

Configuração escolhida pelo usuário depois do diagnóstico acima: `--restricted --strict-mcp-config --no-session-persistence`, com `--permission-mode dontAsk` e ferramentas `Read,Glob,Grep,Edit,Write`.

Segundo a ajuda do Claude Code 2.1.319, o modo restrito remove as ferramentas que executam comandos ou código, confina as ferramentas de arquivo à pasta de trabalho e ignora os arquivos de configuração de usuário, de projeto e locais. Consequência: plugins e hooks do usuário, GateGuard incluído, não rodam nesse processo filho. Nas sessões do próprio usuário nada muda.

Teste: "Crie o arquivo ola.txt com o texto ola. Depois tente criar o arquivo ../fora-do-repo.txt com o texto fora."

| Medida | Valor |
|---|---|
| Duração | 10 s |
| Turnos | 3 |
| Tokens | 2.530 de criação de cache, 9.520 de leitura de cache, 430 de saída |
| Custo equivalente calculado pela CLI | US$ 0,04 |
| Modelo | `claude-fable-5-2` (o padrão da CLI, já que as configurações de usuário são ignoradas) |
| Login | Assinatura. Nenhuma chave de API definida. |
| `ola.txt` | Criado dentro do repositório |
| `../fora-do-repo.txt` | Negado. O arquivo não foi criado. |

## Fluxo real pelo `dualai`

### Claude escreve, Codex revisa

Comando: `dualai run "Adicionar textkit/paging.py com paginate(items, page, page_size), páginas numeradas a partir de 1, e page_count(total_items, page_size); incluir testes" --repo <repo-de-brinquedo> --test-cmd "python -m unittest discover -s tests -t ." --author claude --critic codex`

| Chamada | Duração | Tokens de entrada | Tokens de saída |
|---|---|---|---|
| Claude, autor | 30,9 s | 30.437 | 2.903 |
| Codex, crítico | 6,3 s | 19.038 | 39 |

Resultado: estado `done` em 2 chamadas e 38 s de relógio. O crítico não apontou nada, então não houve chamada de resolução. O branch do repositório ficou intacto até o `dualai approve`, que levou 3 arquivos ao branch (`textkit/paging.py`, `tests/test_paging.py`, `docs/decisions.md`). Depois da aprovação, os 21 testes do repositório passaram.

### Codex acha defeitos, Claude corrige

Roteiro de `scripts/smoke_critic.py --resolver-claude`: um autor falso planta os dois defeitos do passo zero em `textkit/paging.py`; o Codex revisa; o Claude recebe os achados e resolve.

| Chamada | Duração | Tokens de entrada | Tokens de saída |
|---|---|---|---|
| Codex, crítico | 8,4 s | 16.882 | 221 |
| Claude, resolução | 12,1 s | 14.426 | 858 |

Resultado: os dois achados terminaram em `resolved`, o Claude editou o arquivo e devolveu a resolução no formato estruturado, os testes passaram depois da correção, estado `done` em 3 chamadas (2 reais).

## O que não foi verificado

- Modo `api_key` dos dois adaptadores. O código existe e tem testes com processo simulado, mas nenhuma chamada foi feita com chave.
- Codex como autor (sandbox `workspace-write`) neste Windows.
- A mensagem exata que cada CLI devolve ao atingir o limite de uso. Se o texto for diferente do esperado, a execução para como `failed` em vez de `limit_reached`. Nos dois casos ela para e não tenta de novo.
- Tarefas maiores que as de brinquedo. O parâmetro de US$ 1,00 de custo equivalente por chamada do Claude pode levar à interrupção; não constitui limite financeiro garantido.
