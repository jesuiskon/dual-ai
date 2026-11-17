# Dual AI: design da arquitetura B

> **Registro histórico.** Este documento descreve decisões, hipóteses e verificações relatadas na data indicada. Não é um roteiro de instalação nem confirmação de que todas as tarefas foram concluídas. Consulte o [README atual](../../../README.md) para começar e conhecer os limites da cópia preparada. Passos antigos de configuração não devem ser executados automaticamente.

Data: 2026-11-14. Estado: arquitetura B aprovada pelo usuário em conversa, com quatro ajustes já incorporados aqui. Evidência do passo zero: [docs/experiments/2026-11-14-passo-zero-astra-review.md](../../experiments/2026-11-14-passo-zero-astra-review.md).

## 1. Intenção

Hoje o usuário transporta mensagens à mão entre o Claude e o ChatGPT: pede algo a um, copia o resultado para o outro revisar, copia a revisão de volta, e repete. O Dual AI elimina esse transporte. A tarefa é enviada uma vez; o orquestrador leva o trabalho de um modelo ao outro, aplica regras fixas de encerramento e só chama o usuário quando há uma decisão que é dele.

O objetivo é eliminar o trabalho manual entre Claude e Codex, e não construir outro projeto complexo.

## 2. Metas, não-metas e antimetas

**Metas do MVP**

- Uma tarefa de código em um repositório git, enviada uma única vez, passa por autor, revisão cruzada e resolução sem copiar e colar.
- Papéis invertíveis: Claude autor e Codex revisor, ou o contrário.
- Histórico persistente de cada execução: tarefa, diff, testes, achados, decisões, métricas.
- Encerramento garantido: número fixo de chamadas e regras de parada no código.
- Nada entra no branch do usuário sem aprovação explícita dele.
- Modo com agentes falsos, sem nenhuma chamada a modelo, para testar o orquestrador.

**Não-metas do MVP**

- Interface web, banco de dados, APIs pagas, framework multiagente, MCP.
- Mais de dois agentes, debate em várias rodadas, execução em nuvem.
- Modalidades além de revisão de código (ver seção 5: ficam previstas, não implementadas).

**Antimetas** (seria um fracasso mesmo funcionando)

- O usuário continuar sendo chamado a cada tarefa para desempatar ou transportar algo.
- Revisões que reabrem a tarefa indefinidamente.
- Um modelo executar no computador do usuário um comando sugerido pelo outro modelo.
- Depender de um uso das assinaturas que os provedores não permitem.

## 3. Restrições

### Ambiente

- Windows 11, execução local. Python para o orquestrador, só biblioteca padrão em produção; `pytest` como única dependência de desenvolvimento.
- Ferramentas presentes e verificadas em 2026-11-14: Claude Code 2.1.319, Codex CLI 0.167.1 (login ChatGPT, plano Plus), git, Python 3.15, Node, uv. O `gh` não está instalado e não é necessário.

### Regras de uso dos provedores (verificadas em 2026-11-14)

**Claude Code.** Fontes: página "Legal and compliance" da documentação do Claude Code, página "Run Claude Code programmatically", artigo de suporte "Use the Claude Agent SDK with your Claude plan" e Termos de Consumo da Anthropic.

- `claude -p` é o modo programático documentado. Com login de assinatura, o artigo de suporte diz que o Agent SDK e o `claude -p` "still draw from your subscription limits".
- A página legal diz que o login OAuth serve ao "ordinary use of Claude Code and other native Anthropic applications" e que os limites dos planos pressupõem "ordinary, individual usage of Claude Code and the Agent SDK". Ela também diz que nada ali impede o usuário de entrar no binário não modificado do Claude Code com a própria assinatura.
- O que ela proíbe: oferecer login do Claude.ai dentro de aplicativos de terceiros, rotear pedidos de outras pessoas por credenciais de plano, e coletar, guardar ou intermediar credenciais ou tokens de sessão. Quem constrói produtos ou serviços deve usar chave de API.
- Os Termos de Consumo proíbem acesso automatizado "except when you are accessing our Services via an Anthropic API Key or where we otherwise explicitly permit it".
- O modo `--bare` não usa o login de assinatura (exige chave de API), e a documentação avisa que ele se tornará o padrão do `-p` em uma versão futura.
- Planos Max, Ultra e Team incluem créditos mensais de API que cobrem `claude -p` quando executado com chave de API de uma organização do Console vinculada. O plano Pro não inclui.

**Codex CLI.** Fonte: página "Non-interactive mode" da documentação do Codex.

- "`codex exec` reuses saved CLI authentication by default."
- "API keys are the right default for automation", dito no contexto de CI; o login gerenciado pelo ChatGPT em CI é descrito como opção avançada para runners confiáveis. O arquivo `~/.codex/auth.json` deve ser tratado como segredo.
- O padrão do `codex exec` é sandbox somente leitura.

**Leitura adotada (interpretação, não garantia escrita).** Um script pessoal, na máquina do próprio usuário, que chama os binários oficiais não modificados com o login que o usuário já fez, é uso individual comum. O Dual AI fica dentro disso com estas regras, que são parte do design:

1. Só invoca os executáveis oficiais `claude` e `codex` como subprocessos. Não usa SDK com token de assinatura.
2. Nunca lê, copia, grava nem define tokens ou arquivos de credenciais de nenhum provedor.
3. Um único usuário, uma única máquina. Qualquer versão hospedada ou multiusuário exige chave de API e nova verificação das regras.
4. Erro de limite de uso encerra a execução com estado `limit_reached`. Não há repetição automática, espera programada nem troca de conta.
5. Cada adaptador aceita dois modos de autenticação: `subscription` (padrão) e `api_key`. No modo `api_key` o orquestrador apenas deixa a CLI usar a variável de ambiente que o usuário definiu; ele não guarda chaves. Esse é o caminho oficialmente inequívoco se a leitura acima deixar de valer.

A política da Anthropic mudou mais de uma vez em 2026 (uma alteração anunciada foi pausada em 15 de junho). Verificar de novo antes de qualquer uso além do pessoal e local.

## 4. Abordagens consideradas

| | A. `/astra-review` do ECC | B. CLI Python fino sobre `claude -p` e `codex exec` | C. APIs + FastAPI + SQLite + web |
|---|---|---|---|
| Código a escrever | Nenhum | Pequeno | Grande |
| Custo por tarefa | Franquia das assinaturas | Franquia das assinaturas | Pago por token nas duas APIs |
| Inverter papéis | Não | Sim | Sim |
| Histórico e métricas | Não | Sim, em arquivos | Sim |
| Regra de encerramento sob controle | Não (repete até passar, 3 rodadas) | Sim | Sim |
| Outras modalidades no futuro | Não | Sim | Sim |

**Escolhida pelo usuário: B, com A como passo zero.** A provou que a integração funciona nesta máquina e mostrou, na rodada 2, o problema de repetir a revisão até passar. C só passa a fazer sentido com hospedagem em nuvem; os adaptadores de B com modo `api_key` deixam esse caminho aberto.

## 5. Visão preservada: modalidades

Revisão de código é o primeiro caso de uso, não a finalidade da ferramenta. O núcleo do orquestrador conhece apenas uma troca genérica entre dois papéis:

1. **produtor** gera um artefato;
2. **crítico** examina o artefato e devolve achados classificados;
3. **produtor** resolve, justifica ou encaminha cada achado bloqueante.

Uma **modalidade** é um módulo que define quatro coisas: o que o produtor recebe, o que o crítico recebe, o formato dos achados e a verificação objetiva do artefato. O MVP implementa uma só, `code_review`, em que o artefato é um diff e a verificação é o comando de testes. Planejamento, análise de problemas e debate técnico seriam outros módulos com as mesmas quatro peças (por exemplo, artefato igual a um documento de plano e verificação igual a uma lista de critérios).

No MVP não há registro de plugins nem classes abstratas: há um dicionário com uma entrada e funções com a mesma assinatura. Os adaptadores de agente não sabem qual modalidade está rodando.

## 6. Componentes

```
dual-ai/
  pyproject.toml
  src/dualai/
    cli.py                 comandos: run, status, approve, discard, stats
    pipeline.py            a sequência fixa, o teto de chamadas e as paradas
    agents.py              run_claude, run_codex e FakeAgent, mesma assinatura
    findings.py            validação do schema, rebaixamento, estados
    workspace.py           worktree git, diff, comando de testes, merge
    runlog.py              pasta da execução e métricas
    modes/code_review.py   prompts, pacote do crítico, verificação
  tests/
  docs/
```

**Assinatura dos agentes.** Cada adaptador recebe um prompt, um diretório, uma permissão (`read` ou `write`) e, opcionalmente, um JSON Schema para a resposta. Devolve texto ou JSON, tokens usados, duração e um código de resultado (`ok`, `limit_reached`, `timeout`, `error`). O `FakeAgent` devolve respostas roteirizadas e é o que os testes usam.

## 7. Fluxo de uma execução

Comando: `dualai run "<tarefa>" --repo <pasta> --test-cmd "<comando>" [--author claude --critic codex] [--fake]`

1. **Preparar.** Criar um worktree git em `.dualai/worktrees/<id>`, no branch `dualai/<id>`, a partir do HEAD. Criar a pasta da execução.
2. **Chamada 1, produtor.** Recebe a tarefa e trabalha no worktree com permissão de escrita limitada a ele.
3. **Verificação.** O orquestrador gera o diff e roda o comando de testes informado pelo usuário. Modelos não rodam testes nem escolhem o comando.
4. **Chamada 2, crítico.** Sessão nova, somente leitura. Recebe um pacote autossuficiente: a tarefa, o diff, o conteúdo integral dos arquivos alterados (até um teto de tamanho) e a saída dos testes. Não recebe o raciocínio do produtor. Responde em JSON validado por schema.
5. **Triagem no código.** Validar os achados, rebaixar os que não têm cenário de falha e separar bloqueantes de não bloqueantes. Sem bloqueantes, a execução termina aqui com duas chamadas.
6. **Chamada 3, produtor.** Recebe só os achados bloqueantes. Para cada um responde `resolved` (com a alteração feita), `justified` (com o motivo) ou `escalated`.
7. **Verificação final.** O orquestrador roda os testes de novo e fecha a execução conforme a seção 8.
8. **Aprovação.** `dualai approve <id>` leva o branch para o branch do usuário. `dualai discard <id>` remove worktree e branch. Só o usuário executa esses dois comandos.

O pacote do crítico é autossuficiente porque, no passo zero, o Codex neste Windows não conseguiu ler arquivos nem executar comandos sob aprovação `never`.

## 8. Regras de encerramento

### Severidade

| Severidade | Definição operacional | Bloqueia |
|---|---|---|
| `critico` | Perda de dados, falha de segurança, ou o comportamento principal da tarefa não funciona | Sim |
| `relevante` | Resultado errado para entradas dentro do contrato declarado da tarefa, ou critério de aceitação não atendido | Sim |
| `opcional` | Robustez além do contrato declarado, testes de casos de borda, manutenção | Não |
| `preferencia` | Estilo, nomes, gosto | Não |

Todo achado `critico` ou `relevante` precisa trazer arquivo, linha e um cenário concreto: entrada, resultado obtido e resultado esperado. O código rebaixa para `opcional` o achado bloqueante que não traz cenário.

### Estados finais de um achado bloqueante

Cada achado `critico` ou `relevante` termina em exatamente um destes estados:

- `resolved`: o produtor alterou o código e os testes passam depois da alteração;
- `justified`: o produtor recusou com um motivo escrito;
- `escalated`: vai para decisão do usuário.

Achados `opcional` e `preferencia` vão para `backlog.md` da execução e nunca geram chamada nem bloqueiam.

### Estado final da execução

| Estado | Condição |
|---|---|
| `done` | Testes passam e nenhum achado bloqueante ficou sem estado final nem em `escalated` |
| `needs_decision` | Há achado `escalated`, ou os testes falham após a chamada 3 |
| `limit_reached` | Um provedor recusou por limite de uso |
| `failed` | Erro de execução ou resposta inválida depois da única tentativa de reparo |
| `cancelled` | O usuário interrompeu |

### Limites

- Fluxo normal: no máximo 3 chamadas a modelo por tarefa. Não existe segunda revisão.
- Uma única chamada extra é permitida para reparar uma resposta que não valida no schema. Teto absoluto: 4 chamadas.
- Cada chamada tem tempo limite. Ctrl+C encerra o subprocesso em andamento e grava o estado `cancelled`.
- O diff enviado ao crítico tem teto de tamanho; acima dele a execução para em `needs_decision` em vez de cortar o conteúdo em silêncio.

### Decisões do autor deste documento (não foram pedidas pelo usuário)

- Um achado `critico` marcado como `justified` é convertido em `escalated`. Motivo: sem segunda revisão, ninguém confere a justificativa de um crítico, e críticos são raros.
- Um achado `relevante` marcado como `justified` fecha sem chamar o usuário, mas aparece no resumo final com o motivo.
- O produtor não recebe shell na chamada 1 quando é o Claude: só leitura e edição de arquivos. Os testes são sempre do orquestrador.

## 9. Contexto compartilhado e histórico

O contexto compartilhado é o repositório, não a conversa.

- **Convenções do projeto:** `AGENTS.md` (lido pelo Codex) e `CLAUDE.md` (lido pelo Claude) no repositório alvo.
- **Decisões duráveis:** `docs/decisions.md` no repositório alvo, com uma linha acrescentada apenas quando o usuário aprova uma execução.
- **Histórico de execuções:** `.dualai/runs/<id>/` no repositório alvo, fora do controle de versão por `.git/info/exclude`:

| Arquivo | Conteúdo |
|---|---|
| `task.md` | A tarefa como o usuário escreveu |
| `state.json` | Estado, papéis, chamadas usadas, horários |
| `diff.patch`, `tests-1.txt`, `tests-2.txt` | Artefato e verificações |
| `review.json` | Achados do crítico, como vieram e após a triagem |
| `resolution.json` | Estado final de cada achado bloqueante |
| `backlog.md` | Achados não bloqueantes |
| `summary.md` | Resumo para o usuário |
| `metrics.json` | Chamadas, tokens, duração, contagens |

O Claude Mem não faz parte do produto: o Codex não o enxerga, então ele não pode ser fonte comum.

## 10. Segurança e isolamento

- As regras do orquestrador estão no código. Texto vindo de arquivos, diffs ou respostas de modelos é tratado como dado e entra nos prompts delimitado como tal.
- A saída de um modelo só é usada depois de validada por schema. Nenhuma saída de modelo é executada como comando.
- O único comando executado é o `--test-cmd` informado pelo usuário.
- O trabalho acontece em um worktree; o branch do usuário só muda com `approve`.
- Sem chaves de API guardadas. Sem leitura de credenciais.
- Testes do Dual AI usam repositórios de brinquedo. Outros projetos e arquivos privados ficam fora até o usuário decidir o contrário.

## 11. Métricas e experimento de validação

`metrics.json` registra, por execução: chamadas, tokens por chamada, duração, achados por severidade e por estado final, resultado dos testes antes e depois, e quantas decisões foram pedidas ao usuário. `dualai stats` soma as execuções.

**Experimento.** Seis tarefas pequenas em um repositório de brinquedo com testes de aceitação ocultos, cada uma em dois braços (um modelo sozinho; dois modelos). Mais três diffs com defeito plantado e dois limpos, para medir o que o crítico acha e o que ele inventa.

**Regra de decisão proposta.** O modo dual vira padrão se encontrar pelo menos um defeito real a cada três tarefas, com no máximo um bloqueio falso a cada três e no máximo uma decisão do usuário por tarefa. Abaixo disso, fica reservado a mudanças arriscadas.

## 12. Riscos e itens em aberto para quem construir

1. **Claude headless limpo com login de assinatura.** O `claude -p` carrega hooks e plugins do usuário, e o hook GateGuard do ECC bloqueia a primeira escrita de arquivo. O `--bare` evita isso, mas não usa a assinatura. É preciso achar e testar uma forma suportada de iniciar o `claude -p` sem os hooks do usuário mantendo o login.
2. **Mudança anunciada do padrão do `-p` para `--bare`.** Fixar o comportamento esperado e detectar a mudança de versão.
3. **Codex como produtor no Windows.** O sandbox de escrita não foi testado; no passo zero, comandos de shell foram rejeitados sob aprovação `never`.
4. **Custo fixo por chamada.** Cerca de 17 mil tokens de entrada por chamada do Codex vieram de instruções e skills, não do diff. Medir e reduzir sem contornar nada.
5. **Limites das assinaturas.** O plano do Claude do usuário não foi verificado; o do ChatGPT é Plus com janela semanal.
6. **Lançador do Codex no Windows.** A instalação por npm é um shim; o script do ECC resolve chamando `node` com o `codex.js`. O adaptador deve fazer o mesmo e enviar o prompt por stdin.
