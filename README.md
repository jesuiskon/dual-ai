# Dual AI

Dual AI é um protótipo para desenvolver uma tarefa pequena com dois assistentes: um escreve, outro revisa e o primeiro resolve os achados que bloqueiam. Ele chama as CLIs oficiais do Claude Code e do Codex e guarda o resultado para você revisar antes de aprovar a entrada no seu branch Git.

É um projeto pessoal de aprendizado, desenvolvido com assistência de IA.

O MVP tem uma interface local no navegador, uma linha de comando e um modo de ensaio com agentes falsos. Há uma revisão por tarefa, até três chamadas normais e uma chamada adicional apenas para reparar uma resposta em formato inválido. O protótipo não substitui sua revisão de código nem garante que os testes detectem todos os defeitos.

**Versão experimental limitada. Comece pelo ensaio sem modelos.** Ele não exige conta Claude/ChatGPT nem chave de API. O comando de testes ainda roda de verdade no seu computador. O projeto usa a [licença MIT](LICENSE); continua sendo um protótipo, com os limites de segurança e validação descritos abaixo.

## 1. Preparar o ambiente

Para instalar e fazer o ensaio:

- [Git](https://git-scm.com/downloads), com o comando `git` disponível no terminal.
- [Python 3.12 ou mais recente](https://www.python.org/downloads/windows/) e [uv](https://docs.astral.sh/uv/getting-started/installation/). O uv gerencia o ambiente Python do projeto.
- Um navegador para abrir a interface local. Não há instalação de Node ou etapa de build para a interface.

Para chamadas reais, depois do ensaio, também serão necessárias as CLIs oficiais [Claude Code](https://code.claude.com/docs/en/setup) e [Codex CLI](https://developers.openai.com/codex/cli/), instaladas, compatíveis com as opções usadas e autenticadas pelo próprio usuário. Uma instalação npm de CLI pode exigir Node; essa é uma dependência da instalação escolhida, não da interface do Dual AI. Nenhuma CLI de IA é necessária para os testes simulados.

As notas históricas registram Claude Code **2.1.319** e Codex CLI **0.167.1** em chamadas pequenas no Windows 11. Isso não garante funcionamento com versões diferentes. A revisão desta cópia usa agentes falsos e verifica também fluxos da interface no navegador em Linux; não repete chamadas reais nem valida o comportamento atual no Windows. Veja as [limitações](#limitações-e-estado-da-validação).

### Obter a pasta do projeto

Se você recebeu a pasta ou um arquivo exportado, abra o PowerShell nessa pasta: ela deve conter `pyproject.toml`.

Para obter uma cópia pelo Git, abra o repositório de origem do projeto, use **Code → HTTPS** e copie sua URL confirmada. Em uma pasta onde você guarda projetos:

```powershell
$urlDoRepositorio = Read-Host "Cole a URL Git confirmada do repositório Dual AI"
git clone "$urlDoRepositorio" dual-ai
Set-Location dual-ai
```

A partir da pasta do Dual AI, instale as dependências registradas no arquivo de versões:

```powershell
uv sync --frozen
```

Essa etapa baixa dependências da internet. Não chama modelos de IA nem ativa assinatura ou cobrança. O pacote não contém as CLIs dos provedores.

## 2. Criar um repositório descartável de ensaio

O Dual AI trabalha em outro repositório Git e parte do último commit. Um repositório vazio, sem commit, não basta. Use o roteiro abaixo em PowerShell, ainda na pasta do Dual AI. Ele cria uma pasta irmã chamada `dualai-ensaio` e recusa reutilizar uma pasta já existente:

```powershell
$pastaDoDualAI = (Get-Location).Path
$repoDeEnsaio = Join-Path (Split-Path $pastaDoDualAI -Parent) "dualai-ensaio"
if (Test-Path $repoDeEnsaio) { throw "A pasta dualai-ensaio já existe. Escolha outra pasta descartável." }
New-Item -ItemType Directory -Path $repoDeEnsaio | Out-Null
git -C "$repoDeEnsaio" init -b main
git -C "$repoDeEnsaio" config user.name (Read-Host "Nome para os commits deste repositório local de ensaio")
git -C "$repoDeEnsaio" config user.email (Read-Host "Email para os commits deste repositório local de ensaio")
Set-Content -Path (Join-Path $repoDeEnsaio "README.md") -Value "# Repositório local de ensaio" -Encoding utf8
git -C "$repoDeEnsaio" add README.md
git -C "$repoDeEnsaio" commit -m "Preparar repositório local de ensaio"
Write-Output $repoDeEnsaio
```

O nome e o email são configurados somente nesse repositório. Eles passam a fazer parte do commit: não envie esse repositório a terceiros sem revisar sua identidade de autoria. Nenhum comando acima envia conteúdo ao GitHub. Copie o caminho impresso para selecionar a pasta na interface.

## 3. Abrir a interface e fazer o primeiro ensaio

Na pasta do Dual AI:

```powershell
uv run --frozen dualai ui
```

No Windows, depois da instalação, também é possível dar duplo clique em **Dual AI.cmd**. O terminal precisa continuar aberto. A interface usa `127.0.0.1` e a porta padrão `8765`; se o navegador não abrir, use o endereço que o terminal mostrar.

No primeiro uso, a interface começa em modo de ensaio. Uma preferência anterior pode ter sido salva; confira a indicação de ensaio a cada retomada.

1. Abra **Configurações** ou **Escolher repositório**. Selecione a pasta `dualai-ensaio` criada acima, ou cole o caminho e clique em **Usar**.
2. Em **Comando de testes**, escreva `python -c pass` e clique em **Salvar comando**. Esse comando não verifica um programa: serve apenas para observar o fluxo do ensaio.
3. Ative **Modo de ensaio → Usar agentes falsos, sem chamar Claude nem Codex**. Confirme que a conversa mostra **Ensaio, sem modelos** antes de enviar.
4. Feche as configurações e envie uma tarefa neutra, como `Conhecer o fluxo de revisão`. Os agentes falsos seguem um roteiro e criam um arquivo de exemplo; não implementam o pedido livremente.
5. Acompanhe as etapas e leia o diff, os testes e o resultado. **Aprovar** cria um commit e incorpora o exemplo ao branch atual do repositório de ensaio. **Descartar** remove a cópia de trabalho, mantendo o histórico da execução. As duas ações pedem confirmação na interface.

O ensaio altera arquivos locais e pode criar um commit se você aprovar; ele não chama provedores nem consome franquia de IA. Ao usar um projeto real, troque o comando de demonstração pelo comando de testes que já verifica esse projeto.

**Interromper** cancela uma execução em andamento. Fechar a aba não a interrompe: o servidor continua no terminal. Para encerrar o aplicativo, pressione **Ctrl+C** no terminal e aguarde a mensagem de encerramento. Evite matar o processo à força.

Se a porta estiver ocupada:

```powershell
uv run --frozen dualai ui --port 8766
```

Se **Escolher pasta…** falhar por falta do seletor nativo, cole o caminho no campo da interface. Isso não exige instalar uma biblioteca de interface adicional.

## 4. Conferir os testes do Dual AI

Na pasta do aplicativo:

```powershell
uv run --frozen pytest -q
```

Os testes automatizados usam processos simulados e repositórios temporários; não precisam das CLIs de IA e não fazem chamadas reais aos modelos. Eles executam Python, Git e processos locais. Scripts em `scripts/`, especialmente testes de fumaça dos adaptadores reais, são outra coisa: podem chamar provedores. Não os execute como parte de um teste gratuito sem ler seus argumentos e custos.

Se você já tiver Node instalado e quiser validar o JavaScript da interface, existe também este teste opcional, sem dependências npm ou build:

```powershell
node --test tests/test_web_ui.cjs
```

Node é necessário para esse teste JavaScript, não para abrir a interface nem para os testes Python. O teste JavaScript não chama modelos.

## 5. Fazer uma chamada real, se desejar

**Esta etapa pode consumir sua assinatura ou gerar cobrança de API.** Faça login diretamente nas CLIs oficiais, confirme o plano e o modelo disponíveis e revise os preços/configurações antes de começar. O Dual AI não cria contas, contrata planos nem verifica seu saldo junto aos provedores.

Na interface desta versão, o autor é Claude, o revisor é Codex e o modo chamado `subscription` usa o login já salvo em cada CLI. O aplicativo não verifica se esse login é de assinatura ou de uma conta de API, nem se há uso adicional pago habilitado. Só desative **Modo de ensaio** depois de conferir a autenticação e a cobrança nas duas CLIs e escolher um repositório que você aceita enviar aos provedores. Use a linha de comando para mudar papéis, autenticação ou modelo.

A partir da pasta do Dual AI, em PowerShell, informe um projeto pequeno e um comando de testes que você já conhece:

```powershell
$repoReal = Read-Host "Caminho completo da raiz do repositório Git, já com commit"
$comandoDeTestes = Read-Host "Comando de testes conhecido deste projeto"
$tarefa = Read-Host "Tarefa pequena, sem dados pessoais, privados ou segredos"
uv run --frozen dualai run "$tarefa" --repo "$repoReal" --test-cmd "$comandoDeTestes"
```

Por padrão, Claude escreve e Codex revisa. Se as opções de modelo forem omitidas, cada CLI escolhe seu padrão disponível; o aplicativo não garante um modelo específico nem acesso a todos os modelos. Configurações pessoais podem ser ignoradas nos processos filhos. Para escolher explicitamente, use `--claude-model` e/ou `--codex-model` com um identificador que a respectiva CLI reconheça e seu plano permita. A escolha não concede acesso adicional e pode mudar o consumo.

| Opção de `run` | Uso |
|---|---|
| `--repo PASTA` | Raiz do repositório alvo; padrão: pasta atual. |
| `--test-cmd "COMANDO"` | Obrigatória; roda no projeto modificado, com suas permissões locais. |
| `--author claude` ou `--author codex` | Escolhe quem escreve. |
| `--critic codex`, `--critic claude` ou `--critic none` | Escolhe quem revisa, ou executa sem revisor. |
| `--claude-auth subscription` / `--codex-auth subscription` | Usa o login que cada CLI já mantém; padrão. |
| `--claude-auth api_key` / `--codex-auth api_key` | Usa a chave do próprio provedor fornecida pelo ambiente. |
| `--claude-model MODELO` / `--codex-model MODELO` | Seleção opcional de modelo na CLI; depende de acesso e compatibilidade. |
| `--fake` | Ensaio com agentes falsos, sem chamar modelos. |

O resultado imprime um identificador de execução. Para revisar e decidir no terminal, substitua o identificador quando solicitado:

```powershell
uv run --frozen dualai status --repo "$repoReal"
$idDaExecucao = Read-Host "Identificador da execução que você revisou"
uv run --frozen dualai approve "$idDaExecucao" --repo "$repoReal"
```

`approve` cria um commit na cópia de trabalho e tenta incorporá-lo por fast-forward ao branch atual. Uma falha de merge recusa a incorporação ao branch; confira a mensagem antes de tentar novamente. Para descartar uma execução em vez de aprová-la, use `dualai discard` com o mesmo identificador e repositório. A decisão pelo terminal é o próprio comando; ele não apresenta a confirmação visual da interface.

### Login, franquia e custos

- O modo `subscription` usa sessões que as CLIs oficiais já salvaram; o nome da opção não comprova assinatura nem ausência de cobrança. Conforme o login e as configurações do provedor, a chamada pode consumir a franquia do plano, créditos de API ou uso adicional pago. O aplicativo não verifica essa modalidade antes de chamar o agente. Confira a conta e a cobrança diretamente em cada CLI.
- O modo `api_key` usa `ANTHROPIC_API_KEY` para Claude ou `CODEX_API_KEY` para Codex, definidas no ambiente local. Pode haver cobrança por tokens/modelo: confira a organização e a cobrança do provedor. Não coloque valores de chaves em tarefas, código, arquivos versionados, screenshots ou logs. O aplicativo não configura nem autoriza recarga automática.
- Claude recebe o parâmetro de orçamento **US$ 1,00 de custo estimado por chamada** (`CLAUDE_MAX_BUDGET_USD`). É um limite operacional da CLI, não uma garantia de fatura máxima. O registro histórico contém uma chamada que contabilizou valor equivalente acima do parâmetro antes de parar. Em assinatura, esse número é custo equivalente; em API, o consumo pode ser faturável. Codex não recebe esse mesmo teto em dólares. Não há um teto financeiro global validado para toda a tarefa.
- Uma tarefa pode chamar mais de um provedor. O aplicativo limita rodadas; não calcula antecipadamente o preço total nem reserva saldo. Erros reconhecidos como limite encerram a execução, sem espera programada, troca de conta ou nova tentativa para contornar o limite.

## Segurança e privacidade

**Uma cópia Git separa alterações; ela não cria uma máquina ou sandbox para os testes.** O comando de testes pode executar código que um modelo acabou de escrever, com os arquivos, permissões e variáveis de ambiente do seu usuário, antes de você aprovar o commit. Comece em um repositório descartável e não use código de terceiros que você não aceitaria executar. Saiba mais em [SECURITY.md](SECURITY.md).

O autor pode ler o repositório; o revisor recebe tarefa, diff, arquivos alterados e saída dos testes. Em chamadas reais, esses dados são enviados aos provedores escolhidos. A primeira linha da tarefa também vira título visível e, ao aprovar, entra em `docs/decisions.md` e na mensagem de commit. O identificador da execução inclui um trecho normalizado da tarefa e pode aparecer em nomes de branches e registros. Use títulos neutros e revise esse material antes de compartilhar ou fazer push.

Cada execução guarda dados locais em `<repo>/.dualai/runs/<id>/`:

| Arquivo | Conteúdo |
|---|---|
| `summary.md` | Resultado, achados e decisões pendentes. |
| `task.md`, `author.md` | Tarefa original e resposta do autor. |
| `diff.patch`, `diff-initial.patch` | Resultado final preparado para decisão e patch inicial enviado ao revisor, respectivamente. |
| `tests-1.txt`, `tests-2.txt` | Saída dos testes antes e depois da resolução. |
| `review.json`, `resolution.json`, `backlog.md` | Revisão, resolução e itens opcionais. |
| `state.json`, `metrics.json` | Estado e métricas das chamadas. |

O trabalho fica em `<repo>/.dualai/worktrees/<id>/`. O aplicativo acrescenta `.dualai/` às exclusões locais do Git do alvo; isso não criptografa os arquivos, não os remove de backups e não protege `docs/decisions.md` nem commits já feitos. **Descartar** remove a cópia de trabalho; o histórico da execução permanece no disco. Mantenha logs privados e revise qualquer exportação. As configurações da interface ficam em `~/.dualai/ui.json`, com caminhos recentes e comandos de testes.

Os processos dos agentes recebem uma lista limitada de variáveis; somente a chave do próprio provedor é repassada no modo `api_key`. Isso não filtra automaticamente segredos presentes em arquivos, tarefas ou saída de testes. Não compartilhe `.env`, diretórios de autenticação das CLIs, tokens, cookies ou documentos pessoais.

## Limitações e estado da validação

- Há apenas a modalidade de revisão de código e, na interface, uma execução por vez. Não há planejamento/debate livre ou serviço hospedado.
- O trabalho parte do último commit. Alterações locais não commitadas ficam fora da execução e precisam ser revisadas separadamente.
- Execuções antigas sem o registro verificável `reviewed_snapshot` não podem ser aprovadas nesta versão. Não edite o estado para contornar essa verificação.
- Se um encerramento abrupto deixar uma execução marcada como `running`, a interface pode exibi-la como interrompida, mas não libera o descarte automaticamente. É necessário verificar os processos remanescentes e recuperar o estado manualmente, preservando o trabalho; não há comando de recuperação automática nesta versão.
- `done` significa que o fluxo terminou segundo os testes e a triagem; não certifica a correção de tudo. Achados opcionais e preferências não bloqueiam. `needs_decision` pode indicar teste falho ou revisão incompleta por tamanho: leia o resumo antes de aprovar.
- O pacote para o revisor tem limite de 200.000 bytes; acima disso, a execução pede decisão em vez de truncar silenciosamente.
- Os experimentos de 14/11/2026 descrevem poucos casos reais em repositórios de brinquedo no Windows 11. Os testes simulados atuais na nuvem não os repetem e não certificam suporte completo em nenhum sistema. Codex como autor, API paga, mensagens reais de limite e tarefas grandes precisam de validação específica no ambiente escolhido.
- A observação de cerca de 16 mil tokens de entrada do Codex pertence às versões/configuração daquele experimento, não é uma previsão fixa para outras execuções.
- Não há comparação controlada que demonstre ganho de qualidade ou economia em relação a usar um só modelo, nem estatísticas agregadas no comando do MVP.
- Mudanças nas CLIs, modelos, planos ou termos podem exigir adaptação. O projeto não é produto oficial dos provedores e não promete elegibilidade a programas de benefícios.

## Licença

Licenciado sob a [MIT](LICENSE), com copyright **2026 jesuiskon**. A licença permite usar, modificar e redistribuir o projeto, inclusive comercialmente, desde que o aviso de copyright e o aviso de permissão sejam preservados nas cópias ou partes substanciais. O software é fornecido sem garantias, conforme o texto da licença. As CLIs, os serviços e os modelos de terceiros continuam sujeitos aos respectivos termos; esta licença não concede contas, assinaturas ou créditos de IA.

## Documentos e colaboração

- [Como contribuir e validar sem modelos](CONTRIBUTING.md)
- [Segurança, dados locais e relato de problemas](SECURITY.md)
- [Licença MIT](LICENSE)
- [Design histórico do núcleo](docs/superpowers/specs/2026-11-14-dual-ai-design.md)
- [Design da interface](docs/superpowers/specs/2026-11-15-interface-conversacional-design.md)
- [Plano histórico do MVP](docs/superpowers/plans/2026-11-14-dual-ai-mvp.md)
- [Plano da interface](docs/superpowers/plans/2026-11-15-interface-conversacional.md)
- [Experimento inicial de revisão](docs/experiments/2026-11-14-passo-zero-astra-review.md)
- [Relato histórico das chamadas reais](docs/experiments/2026-11-14-chamadas-reais.md)
