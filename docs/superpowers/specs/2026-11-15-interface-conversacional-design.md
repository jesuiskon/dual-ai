# Dual AI v0.2: interface conversacional

> **Registro histórico.** Este documento descreve decisões, hipóteses e verificações relatadas na data indicada. Não é um roteiro de instalação nem confirmação de que todas as tarefas foram concluídas. Consulte o [README atual](../../../README.md) para começar e conhecer os limites da cópia preparada. Passos antigos de configuração não devem ser executados automaticamente.

Data: 2026-11-15. Estado: aprovado pelo usuário em 2026-11-15, com três ajustes já incorporados (interrupção segura, interface responsiva durante as execuções, segurança local). Parte do Dual AI v0.1 (`main`, commit `b882f1d`, 107 testes). O design do orquestrador continua em [2026-11-14-dual-ai-design.md](2026-11-14-dual-ai-design.md) e não é reaberto aqui.

## 1. Intenção

Usar o Dual AI todos os dias sem terminal: abrir o aplicativo, escolher o repositório, escrever a tarefa, acompanhar Claude e Codex trabalhando, ler o parecer e aprovar ou descartar. O histórico continua lá depois de fechar e abrir de novo.

## 2. Metas, não-metas e antimetas

Metas (as seis do escopo pedido, mais a interrupção): chat funcional; seleção de um repositório git existente; envio de tarefas ao orquestrador; progresso real; resultado e histórico; aprovar ou descartar; interromper uma execução em andamento.

Não-metas: novos agentes, troca de papéis pela interface, chat livre, contas, hospedagem, integrações externas, personalização avançada, streaming de tokens.

Antimetas (fariam a entrega falhar mesmo funcionando):

- Duplicar a lógica do orquestrador na interface.
- Mostrar atividade que não aconteceu, ou parecer um chat genérico que o backend não sustenta.
- Sugerir que o ambiente é isolado quando o comando de testes roda com as permissões do usuário.
- Exigir Node, etapa de build ou dezenas de pacotes para uma interface de três arquivos.

## 3. Abordagens

| | A. Gradio | B. React + TypeScript + API Python | C. Servidor Python da biblioteca padrão + HTML, CSS e JavaScript sem build |
|---|---|---|---|
| Dependências novas | Gradio e dezenas de pacotes | Node, npm, Vite, React; etapa de build | Nenhuma |
| Controle visual | Baixo: tema e componentes do Gradio | Total | Total |
| Barra lateral com histórico, identidade por agente, cartão de aprovação | Possível só contornando o framework com CSS e JS injetados | Natural | Natural, escrito à mão |
| Instalar e iniciar no Windows | `uv sync` mais pesado; início lento | Exige Node ou enviar arquivos já compilados | `uv sync` igual ao de hoje |
| Testes | Difícil testar a interface | Segunda pilha de testes (JS) | `pytest` na API; verificação visual no navegador |
| Custo | Aparência genérica de Gradio | Ferramental maior que o próprio backend (1.373 linhas) | Markdown e componentes feitos à mão, cerca de 800 linhas de JS e CSS |

**Escolha: C.** O Gradio entrega um chat pronto, mas a experiência pedida (histórico lateral, três vozes distintas, etapas, cartão de resultado com aprovação) fica fora do que ele faz bem, e a aparência seria a dele, não a do Dual AI. O React daria o mesmo resultado visual que C com muito mais ferramental; a interface tem uma tela e meia dúzia de componentes. C mantém a regra da v0.1 de não ter dependências de produção.

Se a interface crescer muito além deste escopo, migrar o frontend para React continua possível: a API JSON não muda.

## 4. Arquitetura mínima

```
navegador (127.0.0.1) ── JSON ──> dualai.web.server ──> dualai.web.service ──> pipeline.run, approve, discard, RunLog
                                   (http.server)         (funções Python)        (orquestrador v0.1, sem duplicação)
```

Arquivos novos:

| Arquivo | Responsabilidade |
|---|---|
| `src/dualai/web/service.py` | Funções puras sobre o orquestrador: listar execuções, montar a conversa de uma execução a partir da pasta dela, iniciar uma execução em uma thread, aprovar, descartar, guardar repositórios recentes e o comando de testes de cada um |
| `src/dualai/web/server.py` | `ThreadingHTTPServer` em `127.0.0.1`; rotas JSON; arquivos estáticos; verificação de token e de cabeçalho `Host` |
| `src/dualai/web/static/index.html`, `app.css`, `app.js` | A interface |
| `Dual AI.cmd` | Atalho de duplo clique: roda `uv run dualai ui` |

Mudanças pequenas no que existe:

- `cli.py`: comando `dualai ui [--port N] [--no-browser]`, que sobe o servidor e abre o navegador. A lógica de aprovar e descartar sai de `_cmd_approve` e `_cmd_discard` para funções reutilizáveis, usadas pelo terminal e pela interface.
- `pipeline.py`: grava em `state.json` a etapa atual (`stage`: `author`, `check`, `critic`, `resolve`, `recheck`); grava a resposta em texto do autor em `author.md`; aceita um aviso opcional com o id da execução assim que ele existe. Nenhuma regra de fluxo, limite ou parada muda. O crítico continua sem ver a resposta do autor.

Progresso: a interface consulta `GET /api/runs/<id>` uma vez por segundo enquanto a execução está ativa. Cada etapa aparece quando o arquivo correspondente existe na pasta da execução, então o que se vê é o que aconteceu. Sem WebSocket.

Uma execução por vez, em uma thread própria. O servidor atende cada requisição em outra thread, então a página continua respondendo enquanto Claude e Codex trabalham. Enquanto a execução roda, o envio fica bloqueado.

### Interrupção

O botão "Interromper" chama `POST /api/runs/interrupt`. O mecanismo é um sinal de cancelamento (`CancelToken`, em `proc.py`) que a execução carrega:

- quem inicia processos (`run_process`) registra o processo vivo no sinal; ao cancelar, a árvore inteira desse processo é encerrada (o agente ou o comando de testes) e a chamada termina com a exceção `Cancelled`;
- o pipeline confere o sinal antes de cada etapa e trata `Cancelled` como já trata Ctrl+C: grava o estado `cancelled`, o resumo e as métricas;
- a cópia isolada fica como está, para inspeção. Uma execução `cancelled` não pode ser aprovada, só descartada.

Fechar a aba do navegador não interrompe nada: a execução segue no servidor e reaparece ao reabrir a página. Encerrar o aplicativo (Ctrl+C no terminal) aciona o mesmo sinal antes de sair. Se o processo do servidor for morto à força, a execução fica com estado `running` no disco; ao reabrir, ela aparece como "interrompida" e só pode ser descartada.

Mudança no orquestrador: cerca de 40 linhas, sem alterar a ordem das etapas nem os limites.

Configuração da interface: `%USERPROFILE%\.dualai\ui.json`, com repositórios recentes, comando de testes por repositório e tema. O histórico de execuções continua em `<repo>/.dualai/runs/`, como na v0.1.

### API

| Rota | Efeito |
|---|---|
| `GET /api/state` | Repositórios recentes, repositório atual, execução ativa |
| `POST /api/repos/pick` | Abre o seletor de pastas do Windows (processo auxiliar com `tkinter`) e valida que é a raiz de um repositório git |
| `POST /api/repos` | Seleciona um repositório por caminho digitado |
| `PUT /api/repos/settings` | Salva o comando de testes do repositório |
| `GET /api/runs?repo=` | Histórico do repositório |
| `GET /api/runs/<id>?repo=` | Conversa completa de uma execução |
| `POST /api/runs` | Inicia uma execução (`repo`, `task`, `fake`) |
| `POST /api/runs/interrupt` | Interrompe a execução ativa |
| `POST /api/runs/<id>/approve`, `/discard` | Fluxos existentes de aprovação e descarte |

## 5. Proposta visual

Identidade: dois agentes, duas cores, um fundo neutro. A marca são dois círculos sobrepostos.

- **Tema escuro por padrão**, claro disponível por um seletor. Mesmos tokens, dois conjuntos de valores.
- **Fundo** grafite quase preto, superfícies um degrau mais claras, bordas finas. Sem gradientes, sem sombras decorativas.
- **Cores com função:** âmbar para o Claude (autor), verde-água para o Codex (revisor), neutro para o Dual AI (orquestrador). Verde, amarelo e vermelho só para estado (passou, pede decisão, falhou).
- **Tipografia:** Segoe UI Variable para a interface e Cascadia Code ou Consolas para código, ambas do próprio Windows 11. Nenhuma fonte baixada.
- **Conversa em coluna central** de até 760 px. A mensagem do usuário é um bloco discreto à direita. As mensagens dos agentes não têm balão: nome, papel e conteúdo, com uma marca de cor à esquerda.
- **Um único cartão por conversa:** o resultado, com os botões de aprovar e descartar. O resto é texto, listas e blocos recolhíveis (diff, saída dos testes).
- **Movimento discreto:** a etapa em andamento pulsa; mensagens novas entram com um fade curto; botões respondem a hover e clique. Com "reduzir movimento" ativo no sistema, nada anima.

### Telas

1. **Principal.** Barra lateral (marca, "Nova tarefa", histórico com ponto de estado, repositório atual, configurações) e área de conversa com o campo de entrada embaixo.
2. **Estado vazio.** Sem repositório: convite para escolher um. Com repositório e sem conversa aberta: o que o Dual AI faz, em uma frase, e o campo de tarefa.
3. **Repositório e testes** (diálogo). Escolher pasta, comando de testes, e o aviso de que esse comando executa código escrito pelos agentes com as permissões do usuário.
4. **Confirmações** de aprovar e de descartar, dizendo o que vai acontecer com o branch.
5. **Configurações.** Tema, comando de testes do repositório atual, modo de ensaio.

### A conversa de uma execução

| Ordem | Quem | Conteúdo | Origem |
|---|---|---|---|
| 1 | Você | A tarefa | `task.md` |
| 2 | Dual AI | Linha de etapas: Autor, Testes, Revisão, Resolução, Testes | `state.json` |
| 3 | Claude · Autor | Resumo do que fez, arquivos alterados, diff recolhível | `author.md`, `diff.patch` |
| 4 | Dual AI | Testes: passaram ou falharam, saída recolhível | `tests-1.txt` |
| 5 | Codex · Revisor | Achados com severidade, arquivo e linha, cenário e sugestão; ou "nenhum achado" | `review.json` |
| 6 | Claude · Autor | Destino de cada achado e a nota | `resolution.json` |
| 7 | Dual AI | Testes depois da resolução | `tests-2.txt` |
| 8 | Dual AI | Cartão de resultado: estado, decisões pendentes, itens não bloqueantes, aprovar e descartar | `state.json`, `summary.md`, `backlog.md` |

Só aparece o que está registrado. Não há raciocínio interno dos modelos em lugar nenhum.

O campo de entrada diz o que o sistema faz: "Descreva uma tarefa de código para este repositório". Mostra o repositório e o comando de testes em uso. Cada conversa é uma tarefa; depois que ela termina, o campo dá lugar ao cartão de resultado e uma tarefa nova começa em "Nova tarefa".

## 6. Segurança

- Servidor só em `127.0.0.1`. Sem CORS.
- Um token aleatório é gerado a cada início, entregue dentro da própria página e exigido em toda rota `/api`. O cabeçalho `Host` é conferido em toda requisição e o `Origin` em toda escrita. Isso impede que outra página aberta no navegador leia o token ou dispare execuções e aprovações.
- A API só aceita repositórios que o usuário selecionou antes pela interface.
- O comando de testes só muda no diálogo do repositório, digitado pelo usuário, com o aviso ao lado. A interface não aceita comando por tarefa nem vindo de modelo.
- O caminho do repositório é validado como raiz de um repositório git. O id da execução é conferido contra a lista de execuções existentes.
- Todo texto vindo de modelos, do repositório ou dos testes é escapado antes de entrar na página. O Markdown é renderizado por um conversor pequeno próprio, que só gera um conjunto fixo de elementos.
- Nenhuma credencial passa pelo navegador. Os adaptadores e seus limites de chamadas são os da v0.1, sem mudança.
- Abrir ou recarregar a página nunca inicia uma chamada a modelo. Aprovar e descartar exigem clique e confirmação.
- O modo de ensaio usa os agentes falsos da v0.1 e marca a conversa inteira como "Ensaio, sem modelos".

## 7. Verificação

- Os 107 testes atuais continuam passando.
- Testes novos com `pytest` para `service.py` e para as rotas do servidor: token ausente, `Host` errado, repositório inválido, envio duplicado, execução interrompida, aprovar e descartar. Sempre com agentes falsos.
- Verificação visual no navegador com o modo de ensaio em um repositório de brinquedo: estado vazio, execução em andamento, `done`, `needs_decision`, `failed`, tema claro e escuro, teclado.
- Revisão final da interface com as Web Interface Guidelines.
- Nenhuma chamada real a Claude ou Codex é necessária para construir. Uma única execução real ao final, se o usuário quiser, em repositório descartável.

## 8. Decisões do autor deste documento

- Fontes do sistema em vez de fontes empacotadas.
- Conversor de Markdown próprio e pequeno em vez de biblioteca.
- Consulta por intervalo em vez de eventos do servidor.
- Modo de ensaio incluído, porque é a forma de testar a interface sem gastar franquia. No ensaio cada etapa do agente falso espera cerca de um segundo, para que o acompanhamento e a interrupção possam ser vistos; a conversa fica marcada como ensaio.
- "Interromper" age na hora, sem diálogo de confirmação: não destrói nada e a pressa é o motivo de existir.
