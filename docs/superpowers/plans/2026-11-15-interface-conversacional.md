# Dual AI v0.2: interface conversacional — plano de implementação

> **Registro histórico.** Este documento descreve decisões, hipóteses e verificações relatadas na data indicada. Não é um roteiro de instalação nem confirmação de que todas as tarefas foram concluídas. Consulte o [README atual](../../../README.md) para começar e conhecer os limites da cópia preparada. Passos antigos de configuração não devem ser executados automaticamente.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Abrir o Dual AI no navegador, escolher um repositório, enviar uma tarefa, acompanhar as etapas, interromper se preciso, e aprovar ou descartar o resultado, sem terminal.

**Architecture:** Um servidor `http.server` em `127.0.0.1` expõe uma API JSON pequena sobre uma camada de serviço que chama o orquestrador da v0.1 diretamente. A execução roda em uma thread; a página consulta o estado uma vez por segundo. A interface são três arquivos estáticos sem etapa de build.

**Tech Stack:** Python 3.12+ só com biblioteca padrão; `pytest`; HTML, CSS e JavaScript (módulo ES) sem dependências.

**Spec:** [docs/superpowers/specs/2026-11-15-interface-conversacional-design.md](../specs/2026-11-15-interface-conversacional-design.md)

## Global Constraints

- Nenhuma dependência nova, de produção ou de desenvolvimento. Sem Node, sem build.
- A interface não duplica lógica do orquestrador: chama `pipeline.run`, `approve`, `discard` e lê a pasta da execução.
- Servidor só em `127.0.0.1`. Toda rota `/api` exige o token de sessão; `Host` conferido sempre; `Origin` conferido em toda escrita.
- O comando de testes vem só da configuração do repositório. Nenhuma rota aceita comando por tarefa.
- Abrir ou recarregar a página nunca inicia chamada a modelo. Aprovar e descartar exigem confirmação na página.
- Texto de modelos, do repositório e dos testes nunca entra na página por `innerHTML`.
- Só se mostra o que está registrado na pasta da execução. O ensaio é marcado como ensaio.
- Os 107 testes da v0.1 continuam passando. Testes automatizados nunca chamam modelo.
- Limites de chamadas, estados de execução e de achado: os da v0.1, sem mudança. Estado novo só de exibição: `interrupted` (execução `running` no disco sem thread viva).
- Mensagens de commit sem linhas de atribuição.

## Review Focus

1. **Interromper durante o comando de testes ou durante um agente.** Esperado: o processo filho morre, o estado vira `cancelled`, a execução pode ser descartada e não aprovada. Testes na Tarefa 1 e na Tarefa 3.
2. **Dois envios seguidos.** Esperado: o segundo recebe 409 e nenhuma segunda execução começa. Teste na Tarefa 3.
3. **Aplicativo fechado no meio de uma execução.** Esperado: ao reabrir, a execução aparece como `interrupted`, sem botão de aprovar. Teste na Tarefa 3.
4. **Requisição de outra origem ou sem token.** Esperado: 401 ou 403 e nenhum efeito. Testes na Tarefa 4.
5. **Texto de agente com HTML ou Markdown malformado.** Esperado: aparece como texto. Conferido no navegador na Tarefa 6.

## File Structure

| Arquivo | Responsabilidade |
|---|---|
| `src/dualai/proc.py` | Ganha `CancelToken` e `Cancelled` |
| `src/dualai/agents.py` | `AgentRequest.cancel`; adaptadores repassam o sinal; agentes falsos saem do `cli.py` para cá |
| `src/dualai/workspace.py` | `run_check(..., cancel=None)`; `require_repo_root` público |
| `src/dualai/pipeline.py` | `RunConfig.cancel`, `RunConfig.on_start`, etapa em `state.json`, `author.md` |
| `src/dualai/actions.py` | `approve_run`, `discard_run`, `require_run`, `ActionError`: usados pelo terminal e pela interface |
| `src/dualai/web/service.py` | Camada de serviço: repositórios, histórico, detalhe da execução, iniciar, interromper, decidir |
| `src/dualai/web/server.py` | Servidor HTTP, rotas, token, `Host`, `Origin`, arquivos estáticos |
| `src/dualai/web/static/` | `index.html`, `app.css`, `app.js` |
| `src/dualai/cli.py` | Comando `ui` |
| `Dual AI.cmd` | Atalho de duplo clique |

---

### Task 1: Interrupção e etapas no orquestrador

**Files:** modify `proc.py`, `agents.py`, `workspace.py`, `pipeline.py`; test `tests/test_cancel.py`; ajustar `tests/test_cli.py` (lista de arquivos da execução ganha `author.md`).

**Interfaces (produces):**
- `proc.Cancelled(Exception)`; `proc.CancelToken` com `cancel()`, `is_set() -> bool`, `wait(seconds) -> bool`, `attach(process)`, `detach()`.
- `run_process(..., cancel: CancelToken | None = None)`: lança `Cancelled` se o sinal já estiver ativo ou for ativado durante o processo, depois de encerrar a árvore.
- `AgentRequest(..., cancel: CancelToken | None = None)`; `run_codex` e `run_claude` chamam `spawn(..., cancel=request.cancel)`.
- `run_check(worktree, test_cmd, timeout_s, cancel=None)`.
- `RunConfig(..., cancel: CancelToken | None = None, on_start: Callable[[str], None] | None = None)`.
- `state.json` ganha `stage` (`author`, `check`, `critic`, `resolve`, `recheck`, `closed`) e `tests_1_pass`. A pasta da execução ganha `author.md` (texto de resposta do autor na chamada 1).

- [ ] **Step 1: testes que falham** em `tests/test_cancel.py`: `test_cancel_kills_a_running_process_tree` (processo de 30 s termina com `Cancelled` em menos de 15 s); `test_a_cancelled_token_refuses_to_start_a_process`; `test_interrupt_between_stages_ends_the_run_as_cancelled` (o autor falso aciona o sinal; crítico não é chamado; `state.json` com `cancelled`); `test_interrupt_kills_the_test_command` (comando de 30 s, sinal após 1 s, execução `cancelled` em menos de 15 s); `test_adapters_hand_the_token_to_the_process`; `test_stage_and_author_text_are_recorded` (durante a chamada do crítico `stage == "critic"`; ao final `author.md` tem o texto do autor e `tests_1_pass` é verdadeiro); `test_on_start_reports_the_run_id_before_the_first_call`.
- [ ] **Step 2:** `uv run pytest tests/test_cancel.py -q`. Expected: erro de importação.
- [ ] **Step 3:** implementar. O pipeline confere o sinal antes de cada etapa e trata `Cancelled` como trata `KeyboardInterrupt`.
- [ ] **Step 4:** `uv run pytest -q`. Expected: tudo passa.
- [ ] **Step 5:** commit `feat: interrupção segura e etapa atual registrada na execução`.

### Task 2: Ações compartilhadas

**Files:** create `src/dualai/actions.py`; modify `cli.py`, `agents.py`; test `tests/test_actions.py`.

**Interfaces (produces):** `ActionError(Exception)`; `APPROVABLE = ("done", "needs_decision")`; `require_run(repo: Path, run_id: str) -> RunLog` (recusa id com separador de caminho e execução inexistente); `approve_run(repo, run_id) -> None`; `discard_run(repo, run_id) -> None`; em `agents.py`, `fake_author(request)` e `fake_critic(request)`.

- [ ] **Step 1: testes que falham:** `test_run_id_with_a_path_is_rejected` (`"../x"` e `"a/b"` lançam `ActionError`); `test_approve_and_discard_work_without_the_cli` (execução falsa pelo pipeline, `approve_run` leva `DUALAI_FAKE.txt` ao repositório e grava `decision`; outra execução, `discard_run`).
- [ ] **Step 2:** rodar e ver falhar por importação.
- [ ] **Step 3:** mover a lógica de `_cmd_approve` e `_cmd_discard` para `actions.py`; o `cli.py` passa a chamá-la e a tratar `ActionError`.
- [ ] **Step 4:** `uv run pytest -q`. Expected: tudo passa, inclusive os testes de terminal existentes.
- [ ] **Step 5:** commit `refactor: aprovar e descartar em funções usadas pelo terminal e pela interface`.

### Task 3: Camada de serviço

**Files:** create `src/dualai/web/__init__.py`, `src/dualai/web/service.py`; test `tests/test_web_service.py`.

**Interfaces (produces):**
- `ServiceError(Exception)` com `status: int` e mensagem.
- `Service(config_path: Path, fake_delay_s: float = 1.0, agents: dict | None = None, picker: Callable[[], str] | None = None)`.
- Métodos, todos devolvendo dicionários serializáveis: `state()`, `select_repo(path)`, `pick_repo()`, `save_settings(repo, test_cmd)`, `list_runs(repo)`, `run_detail(repo, run_id)`, `start_run(repo, task, fake=False)`, `interrupt()`, `approve(repo, run_id)`, `discard(repo, run_id)`, `shutdown()`.
- `run_detail` devolve: `id`, `status` (com `interrupted`), `stage`, `reason`, `task`, `author`, `critic`, `fake`, `calls_used`, `seconds`, `tests_pass`, `tests_1_pass`, `decision`, `uncommitted_ignored`, `author_text`, `files`, `diff`, `tests_1`, `tests_2`, `review`, `resolution`, `test_cmd`, `can_decide`, `can_discard`, `interrupting`.

Regras: `repo` precisa ter sido selecionado antes (senão 400); tarefa vazia ou acima de 20.000 caracteres: 400; sem comando de testes: 400; execução ativa: 409; papéis fixos (autor `claude`, crítico `codex`, autenticação `subscription`), ou os agentes falsos com pausa quando `fake`; saídas de testes limitadas aos últimos 20.000 caracteres e diff a 300.000.

- [ ] **Step 1: testes que falham:** repositório inválido; repositório não selecionado; envio sem comando de testes; execução de ensaio completa (detalhe com `author_text`, `files == ["DUALAI_FAKE.txt"]`, um achado bloqueante, resolução, `done`, `can_decide`); `approve` e `discard`; envio duplicado dá 409; `interrupt` leva a `cancelled`, com `can_decide` falso e `can_discard` verdadeiro; `state.json` com `running` e sem thread aparece como `interrupted`; id com caminho é recusado; histórico e configuração sobrevivem a uma nova instância de `Service`; `shutdown` interrompe a execução ativa; tarefa vazia.
- [ ] **Step 2:** rodar e ver falhar por importação.
- [ ] **Step 3:** implementar.
- [ ] **Step 4:** `uv run pytest -q`. Expected: tudo passa.
- [ ] **Step 5:** commit `feat: camada de serviço da interface`.

### Task 4: Servidor local e comando `ui`

**Files:** create `src/dualai/web/server.py`; modify `cli.py`; create `Dual AI.cmd`; test `tests/test_web_server.py`.

**Interfaces (produces):** `make_server(service, port=8765) -> ThreadingHTTPServer` com atributo `token`; `serve(port, open_browser, config_path) -> int`; comando `dualai ui [--port N] [--no-browser] [--config ARQUIVO]`.

Rotas: as da tabela de API do design. Respostas de erro: `{"error": "<mensagem>"}`. Cabeçalhos em toda resposta: `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, e na página uma Content-Security-Policy `default-src 'self'; frame-ancestors 'none'`. Corpo aceito: JSON de até 1 MB. Arquivos estáticos: lista fechada de nomes.

- [ ] **Step 1: testes que falham:** a página traz o token e a CSP; `/api/state` sem token: 401; `Host` estranho: 403; `POST` com `Origin` de outro site: 403; nome de arquivo estático fora da lista: 404; rota inexistente: 404; fluxo por HTTP (selecionar repositório, salvar comando, iniciar ensaio, consultar até `done`, aprovar); `POST /api/runs/interrupt` sem execução ativa responde sem erro.
- [ ] **Step 2:** rodar e ver falhar por importação.
- [ ] **Step 3:** implementar o servidor e o comando.
- [ ] **Step 4:** `uv run pytest -q`. Expected: tudo passa.
- [ ] **Step 5:** commit `feat: servidor local e comando dualai ui`.

### Task 5: A interface

**Files:** create `src/dualai/web/static/index.html`, `app.css`, `app.js`.

Segue a seção 5 do design e o esboço aprovado: barra lateral (marca, "Nova tarefa", histórico com ponto de estado, repositório, configurações); coluna de conversa de até 760 px; três vozes (âmbar para o autor, verde-água para o revisor, neutro para o Dual AI); linha de etapas; um único cartão, o de resultado; campo de entrada que só aparece em "Nova tarefa"; barra de execução com "Interromper"; diálogo de configurações (tema, ensaio, repositório, comando de testes com o aviso de risco); diálogo de confirmação para aprovar e descartar. Tema escuro por padrão, claro pelo seletor. Sem `innerHTML` para conteúdo. Respeita `prefers-reduced-motion`.

- [ ] **Step 1:** escrever os três arquivos.
- [ ] **Step 2:** `uv run pytest -q` (o teste da página da Tarefa 4 continua passando).
- [ ] **Step 3:** commit `feat: interface conversacional`.

### Task 6: Verificação visual, revisão e documentação

- [ ] **Step 1:** subir `dualai ui` com `--config` apontando para um arquivo temporário e um repositório de brinquedo; no navegador, conferir: estado vazio, seleção de repositório, ensaio em andamento com as etapas, interrupção, `done` com aprovação, `needs_decision` (comando de testes que falha), descarte, uma execução `failed` fabricada, tema claro, teclado, largura estreita, texto de agente com HTML.
- [ ] **Step 2:** revisar com as Web Interface Guidelines e corrigir o que for encontrado.
- [ ] **Step 3:** atualizar o README (como iniciar a interface, interrupção, segurança).
- [ ] **Step 4:** `uv run pytest -q`; commit `docs: interface conversacional no README` (ou junto com as correções da revisão).
