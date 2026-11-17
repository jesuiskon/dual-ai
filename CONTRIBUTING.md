# Como contribuir com o Dual AI

Dual AI é uma versão experimental limitada, distribuída sob a [licença MIT](LICENSE). Você pode estudar, usar, modificar e compartilhar o projeto nos termos dessa licença. Preserve os avisos de copyright e de permissão e respeite os direitos de terceiros.

Use o repositório de origem do projeto para propor melhorias ou relatar problemas. Prefira mudanças pequenas e reproduções com agentes falsos. A integração de uma contribuição depende da revisão do responsável pelo projeto; a licença não garante aceitação de mudanças.

## Começar sem chamar modelos

Na pasta de uma cópia de desenvolvimento autorizada, com Git, Python 3.12 ou superior e uv disponíveis:

```text
uv sync --frozen
uv run --frozen pytest -q
```

O primeiro comando pode baixar dependências. A suíte automatizada usa agentes falsos e processos simulados; não precisa de login em Claude ou Codex e não faz chamadas pagas a modelos. Testes de servidor podem abrir uma porta local temporária.

Para verificar também o JavaScript da interface, há uma suíte opcional que usa o executor de testes do Node, sem pacotes npm nem build:

```text
node --test tests/test_web_ui.cjs
```

Node precisa estar instalado somente para essa verificação JavaScript ou para uma instalação de CLI de provedor que o exija. A interface do aplicativo e a suíte Python não passam a depender dele. Essa suíte JavaScript não chama modelos.

Para experimentar o fluxo, use o modo de ensaio `--fake` em um repositório descartável com pelo menos um commit. Siga o exemplo do README e confira o caminho antes de rodar. O comando de testes informado em `--test-cmd` executa código com as permissões do usuário, inclusive no ensaio.

Não trate scripts de fumaça com provedores reais como parte da suíte gratuita. Qualquer execução real de Claude, Codex ou API exige apresentar antes a finalidade, as chamadas previstas, o consumo ou custo estimado e obter aprovação específica. Não habilite cobrança, recarga ou outro serviço pago para conseguir rodar um teste.

## O que preservar nas mudanças

- O fluxo continua sendo autor, testes, uma única revisão, resolução quando necessária e novos testes. O limite é de quatro chamadas a modelo, incluindo eventual reparo de formato; não deve surgir uma segunda revisão automática.
- O resultado só entra no branch alvo quando o usuário aprova. Uma falha deve aparecer como falha, com informações suficientes para retomar a análise.
- Agentes falsos devem continuar permitindo verificar o comportamento sem consumir franquia. Não torne um teste unitário dependente de conta, assinatura, chave ou resposta real de modelo.
- Não inclua chaves, tokens, cookies, arquivos de credenciais, documentos pessoais ou dados de trabalho privado. Revise também fixtures, imagens, caminhos locais e saídas de erro.
- Não envie a pasta `.dualai/`, histórico de sessões ou logs brutos junto com uma contribuição. Use exemplos inventados e trechos mínimos já revisados para remover dados privados.
- Preserve os avisos de terceiros que forem aplicáveis. Copiar código de outro projeto exige verificar sua origem e licença antes da inclusão.

## Relatar um problema

Para abrir um relato no repositório do projeto, use um exemplo pequeno e reproduzível:

```text
Objetivo: o que eu queria fazer.
Ambiente: sistema, versão do Python/uv e versão do Dual AI.
Passos: como reproduzir, de preferência com --fake e um repositório descartável.
Esperado: qual comportamento deveria ocorrer.
Observado: o que aconteceu, sem segredos ou logs brutos.
Verificação: teste ou comando gratuito que confirma o problema.
```

Se o problema depender de uma CLI real, informe a versão e descreva o resultado observado sem refazer uma chamada paga apenas para completar o relato. Não compartilhe dados de outra pessoa ou organização.

Uma proposta de correção deve explicar o comportamento que muda e demonstrar que o objetivo do fluxo foi preservado. Acrescente um teste quando ele verificar uma falha relevante; mudanças simples de texto não precisam de testes que apenas repitam o texto. Afirmações de qualidade, economia ou superioridade sobre um agente sozinho precisam de comparação reproduzível, não apenas de uma execução bem-sucedida.

Antes de enviar uma contribuição, revise o diff, os arquivos incluídos e a saída dos testes. Inclua apenas material que você pode compartilhar sob a licença do projeto; preserve avisos de terceiros e não envie dados privados. Para possíveis falhas de segurança, siga [SECURITY.md](SECURITY.md).
