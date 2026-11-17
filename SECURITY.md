# Segurança e privacidade

Dual AI é um protótipo local e uma versão experimental limitada, distribuída sob a [licença MIT](LICENSE), sem garantias conforme o texto da licença. Não há serviço hospedado, equipe de segurança ou canal privado de contato publicado por este documento. Os pontos abaixo explicam os limites que o usuário precisa conhecer antes de executar um projeto real.

## O que o isolamento faz

O trabalho dos agentes fica em um Git worktree, separado do branch atual. O branch recebe alterações depois de uma aprovação, por fast-forward quando possível. Isso organiza a revisão; não cria uma máquina separada para executar código.

**O comando de testes executa código do projeto modificado com as permissões e o ambiente do usuário.** Pode ler arquivos acessíveis, receber variáveis de ambiente e acessar serviços de acordo com o sistema. Ele roda antes da aprovação do commit. Os limites das ferramentas dos agentes não são aplicados automaticamente a esse comando. Até no ensaio, o comando de testes roda de verdade.

Use um repositório descartável para aprender. Só execute testes de um projeto em que você aceitaria rodar aquele código. A tarefa não deve pedir que um agente examine chaves, documentos pessoais, sessões ou diretórios de autenticação.

## Agentes, ferramentas e autenticação

- O aplicativo chama as CLIs oficiais `claude` e `codex`; o usuário instala e autentica cada uma conforme a documentação do provedor. Ele não cria um login intermediário nem importa credenciais de outra pessoa.
- Os subprocessos dos agentes recebem uma lista limitada de variáveis de ambiente; em `api_key`, a chave do próprio provedor é incluída. Isso não impede que código executado pelos testes use o ambiente normal do usuário.
- O adaptador Claude pede `--restricted`, ferramentas de arquivo e configurações para não persistir a sessão da chamada. As notas históricas explicam que esse processo filho pode ignorar configurações, plugins e hooks pessoais. Não trate seus hooks pessoais como uma proteção adicional garantida nessa execução.
- O adaptador Codex pede aprovação `never` e sandbox `read-only` para revisar ou `workspace-write` sem rede para escrever. A compatibilidade dessas opções e a sua aplicação dependem da CLI e do sistema. Não troque automaticamente por permissões mais amplas para fazer uma chamada funcionar.
- Selecionar um modelo depende das permissões e do plano do usuário. Consumo de assinatura, API e uso adicional são responsabilidades da conta do provedor; o orçamento informado à CLI não é uma garantia de fatura máxima.

## Conteúdo que pode sair do computador

Em uma chamada real, o autor recebe a tarefa e pode ler o repositório. O revisor recebe tarefa, diff, conteúdo dos arquivos alterados e saída dos testes. Esses dados são enviados aos provedores escolhidos. Uma mensagem de erro ou uma linha de teste pode conter um segredo: a limitação de variáveis do processo não faz remoção automática desse conteúdo.

Não use dados de clientes, código corporativo, documentos pessoais ou outro material que você não tem autorização para compartilhar com os provedores. Não coloque chaves em prompts. Não envie diretórios de autenticação, cookies, tokens ou arquivos `.env` em um relato de problema.

## Registros locais e Git

Cada execução mantém tarefa, respostas, diff e testes em `.dualai/runs/`. A cópia de trabalho fica em `.dualai/worktrees/`. Esses arquivos são locais e não são criptografados pelo aplicativo. Exclusão do Git não os exclui de backups, sincronização de pastas ou acesso de outros programas.

O identificador de execução incorpora um trecho normalizado da tarefa; ele pode aparecer em nomes de branches e caminhos. **Ao aprovar, a primeira linha da tarefa também é gravada em `docs/decisions.md` e na mensagem de commit.** Esse conteúdo passa a ser versionado e pode tornar-se público em um push. Escreva títulos neutros e confira o commit, o histórico e `docs/decisions.md` antes de compartilhar.

Descartar uma execução remove o worktree; o histórico permanece no disco para consulta. Não suponha que o descarte apagou tarefas ou respostas antigas. A configuração da interface em `~/.dualai/ui.json` guarda caminhos recentes e comandos de testes, que também podem conter contexto privado.

O `.gitignore` da pasta do aplicativo ajuda a evitar o envio acidental de arquivos sensíveis. Ele não limpa arquivos já rastreados ou commits antigos, nem altera automaticamente as exclusões de todos os repositórios em que você trabalhar. Antes de compartilhar, examine `git status`, os arquivos rastreados e o histórico. Um dado já enviado precisa de tratamento próprio; apagar só a cópia atual não o remove dos commits anteriores.

## Servidor da interface

A interface escuta em `127.0.0.1`, usa um token de sessão e verifica cabeçalhos de requisição. Essas proteções se destinam ao uso local. Não exponha a porta à internet, não configure acesso multiusuário ou proxy público com base neste protótipo. Não compartilhe o token da página, respostas da API ou o arquivo de configuração sem inspeção.

Fechar a aba não encerra a execução. Use **Interromper** para cancelar uma tarefa e **Ctrl+C** no terminal para encerrar o aplicativo. Se o processo for morto à força, verifique processos e estado remanescentes antes de retomar. Um registro órfão ainda marcado como `running` exige recuperação manual e não fica descartável automaticamente. Não edite o estado apenas para liberar aprovação ou descarte. A revisão do diff continua necessária depois de qualquer falha.

## Relatar um problema

Use os canais confirmados pelo responsável no repositório de origem do projeto. Se houver um canal privado de segurança, prefira-o para falhas que exponham dados ou permitam acesso indevido. Este documento não informa um endereço privado de contato; não envie segredos em issues públicas.

Uma reprodução deve usar repositório de brinquedo, nomes neutros, versões do Python/Git/CLIs e passos pequenos. Remova chaves, dados pessoais, caminhos de usuário, identificadores de sessão e conteúdo privado dos anexos. Não publique `.dualai/` inteira nem arquivos de autenticação. Se não houver canal privado confirmado, aguarde um meio de contato antes de expor detalhes sensíveis em um canal público.
