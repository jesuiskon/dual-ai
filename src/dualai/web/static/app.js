// Dual AI: a página. Sem dependências e sem etapa de build.
// Regra da casa: texto vindo do servidor entra só por textContent ou nós de texto, nunca por innerHTML.

const TOKEN = document.querySelector('meta[name="dualai-token"]').content;
const $ = (id) => document.getElementById(id);

const prefs = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem(`dualai.${key}`);
      return raw === null ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try { localStorage.setItem(`dualai.${key}`, JSON.stringify(value)); } catch { /* preferência, não estado */ }
  },
};

const view = {
  state: null,          // resposta de /api/state
  runs: [],             // histórico do repositório atual
  runId: null,          // conversa aberta; null = nova tarefa
  runRepo: null,        // o id só identifica uma conversa junto com seu repositório
  navigation: 0,       // ignora respostas que chegaram depois de uma troca de conversa
  run: null,            // detalhe da conversa aberta
  runJson: '',
  dockKey: '',
  open: new Set(),      // blocos recolhíveis abertos, por chave
  fake: prefs.get('fake', true) !== false,
  busy: false,
  timer: null,
  error: '',
};

// --- pequenos utilitários de DOM ---------------------------------------------

function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child);
  }
  return node;
}

const ICONS = {
  plus: ['M12 5v14', 'M5 12h14'],
  folder: ['M3 7.5A1.5 1.5 0 0 1 4.5 6h4.4l2 2.2h8.6A1.5 1.5 0 0 1 21 9.7v8.8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z'],
  sliders: ['M4 7h9', 'M17 7h3', 'M4 17h3', 'M11 17h9', 'M15 5a2 2 0 1 0 0 4a2 2 0 0 0 0-4z', 'M9 15a2 2 0 1 0 0 4a2 2 0 0 0 0-4z'],
  send: ['M12 19V6', 'M6.5 11.5L12 6l5.5 5.5'],
  stop: ['M7.5 7.5h9v9h-9z'],
  check: ['M5 12.5l4.5 4.5L19 7.5'],
  chevron: ['M9 6l6 6-6 6'],
  x: ['M6 6l12 12', 'M18 6L6 18'],
  menu: ['M4 7h16', 'M4 12h16', 'M4 17h16'],
  terminal: ['M5 8l4 4-4 4', 'M12 16h7'],
};

function icon(name, size = 16) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  const attrs = { class: 'icon', viewBox: '0 0 24 24', width: size, height: size, fill: 'none', stroke: 'currentColor',
    'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' };
  for (const [key, value] of Object.entries(attrs)) svg.setAttribute(key, value);
  for (const d of ICONS[name]) {
    const path = document.createElementNS(ns, 'path');
    path.setAttribute('d', d);
    svg.append(path);
  }
  return svg;
}

let toastTimer = null;
function toast(message) {
  const node = $('toast');
  node.textContent = message;
  node.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove('show'), 3200);
}

// --- API ---------------------------------------------------------------------

async function api(method, path, body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: { 'X-Dualai-Token': TOKEN, ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error('O Dual AI não respondeu. Confira se ele continua aberto no terminal.');
  }
  let data = {};
  try { data = await response.json(); } catch { /* resposta sem corpo */ }
  if (!response.ok) throw new Error(data.error || `Erro ${response.status}`);
  return data;
}

const repo = () => (view.state && view.state.current) || null;
const repoQuery = () => `repo=${encodeURIComponent(repo().path)}`;

// --- textos ------------------------------------------------------------------

const STATUS = {
  running: ['Em andamento', 'b-run'],
  done: ['Concluída', 'b-ok'],
  needs_decision: ['Pede sua decisão', 'b-warn'],
  limit_reached: ['Limite de uso atingido', 'b-warn'],
  failed: ['Falhou', 'b-danger'],
  cancelled: ['Interrompida', 'b-neutral'],
  interrupted: ['Interrompida', 'b-neutral'],
};
const STAGES = [['author', 'Autor'], ['check', 'Testes'], ['critic', 'Revisão'], ['resolve', 'Resolução'], ['recheck', 'Testes']];
const REASONS = {
  no_changes: 'O autor não alterou nenhum arquivo.',
  packet_too_large: 'As alterações passaram do tamanho máximo para revisão. Leia o diff antes de decidir.',
  invalid_output: 'Um agente respondeu fora do formato combinado, mesmo depois de uma tentativa de reparo.',
};
const SEVERITY = { critico: 'b-danger', relevante: 'b-warn', opcional: 'b-neutral', preferencia: 'b-neutral' };
const RESOLUTION = {
  resolved: ['resolvido', 'b-ok'],
  justified: ['justificado', 'b-neutral'],
  escalated: ['encaminhado a você', 'b-warn'],
};
const NAMES = { claude: 'Claude', codex: 'Codex' };

const authorName = (run) => (run.fake ? 'Autor de ensaio' : NAMES[run.author] || run.author || 'Autor');
const criticName = (run) => (run.fake ? 'Revisor de ensaio' : NAMES[run.critic] || run.critic || 'Revisor');
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

function stageNow(run) {
  if (run.interrupting) return 'Interrompendo…';
  return {
    author: `${authorName(run)} está escrevendo`,
    check: 'Rodando os testes',
    critic: `${criticName(run)} está revisando`,
    resolve: `${authorName(run)} está resolvendo os achados`,
    recheck: 'Rodando os testes de novo',
  }[run.stage] || 'Preparando a cópia isolada';
}

function whenOf(runId) {
  const match = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(runId);
  return match ? `${match[3]}/${match[2]} ${match[4]}:${match[5]}` : '';
}

// --- Markdown mínimo, construído como nós do DOM -----------------------------

function inline(text) {
  return text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/).filter(Boolean).map((part) => {
    if (part.length > 2 && part.startsWith('`') && part.endsWith('`')) return h('code', { text: part.slice(1, -1) });
    if (part.length > 4 && part.startsWith('**') && part.endsWith('**')) return h('strong', { text: part.slice(2, -2) });
    return part;
  });
}

function markdown(text) {
  const root = h('div', { class: 'md' });
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  let paragraph = [];
  let list = null;
  const flush = () => {
    if (paragraph.length) root.append(h('p', {}, inline(paragraph.join(' '))));
    paragraph = [];
    list = null;
  };
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (line.trimStart().startsWith('```')) {
      flush();
      const code = [];
      for (i += 1; i < lines.length && !lines[i].trimStart().startsWith('```'); i += 1) code.push(lines[i]);
      root.append(h('pre', {}, h('code', { text: code.join('\n') })));
      continue;
    }
    const heading = /^#{1,6}\s+(.*)$/.exec(line);
    const item = /^\s*(?:[-*+]|(\d+)[.)])\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      root.append(h('h4', {}, inline(heading[1])));
    } else if (item) {
      if (paragraph.length) { root.append(h('p', {}, inline(paragraph.join(' ')))); paragraph = []; }
      const tag = item[1] ? 'ol' : 'ul';
      if (!list || list.tagName.toLowerCase() !== tag) { list = h(tag); root.append(list); }
      list.append(h('li', {}, inline(item[2])));
    } else if (!line.trim()) {
      flush();
    } else {
      list = null;
      paragraph.push(line.trim());
    }
  }
  flush();
  return root;
}

function diffBlock(text) {
  const pre = h('pre', { class: 'diff' });
  for (const line of text.split('\n')) {
    let kind = '';
    if (line.startsWith('@@')) kind = 'hunk';
    else if (/^(diff --git|index |\+\+\+ |--- |new file|deleted file)/.test(line)) kind = 'meta';
    else if (line.startsWith('+')) kind = 'add';
    else if (line.startsWith('-')) kind = 'del';
    pre.append(kind ? h('span', { class: kind, text: line }) : line, '\n');
  }
  return pre;
}

// Bloco recolhível que só monta o conteúdo quando é aberto e lembra se estava aberto.
function fold(key, label, build) {
  const body = h('div', { class: 'fold-body' });
  const details = h('details', { class: 'fold' }, h('summary', {}, icon('chevron', 14), label), body);
  const fill = () => { if (!body.hasChildNodes()) body.append(build()); };
  if (view.open.has(key)) { details.open = true; fill(); }
  details.addEventListener('toggle', () => {
    if (details.open) { view.open.add(key); fill(); } else view.open.delete(key);
  });
  return details;
}

const badge = (label, kind) => h('span', { class: `badge ${kind}`, text: label });

// --- barra lateral -----------------------------------------------------------

function renderSidebar() {
  const history = $('history');
  history.replaceChildren();
  if (!repo()) {
    history.append(h('p', { class: 'history-empty', text: 'Escolha um repositório para ver as tarefas.' }));
  } else if (!view.runs.length) {
    history.append(h('p', { class: 'history-empty', text: 'Nenhuma tarefa ainda neste repositório.' }));
  }
  for (const run of view.runs) {
    const [label] = STATUS[run.status] || [run.status];
    const decided = run.decision === 'approved' ? ' · aprovada' : run.decision === 'discarded' ? ' · descartada' : '';
    history.append(h('button', {
      class: 'run-item', type: 'button', 'aria-current': run.id === view.runId ? 'true' : null,
      onclick: () => { closeNav(); openRun(run.id); },
    },
    h('span', { class: `dot s-${run.status}`, 'aria-hidden': 'true' }),
    h('span', { class: 'run-title', text: run.title }),
    h('span', { class: 'run-meta', text: `${label}${decided}${run.fake ? ' · ensaio' : ''} · ${whenOf(run.id)}` })));
  }

  $('repo-button').replaceChildren(icon('folder'),
    h('span', { class: `grow${repo() ? '' : ' muted'}`, text: repo() ? repo().name : 'Escolher repositório' }),
    icon('chevron', 14));
  $('repo-button').setAttribute('aria-label', repo() ? `Repositório: ${repo().name}. Trocar ou configurar` : 'Escolher repositório');
  $('settings-button').replaceChildren(icon('sliders'), h('span', { class: 'grow', text: 'Configurações' }));
}

// --- cabeçalho ---------------------------------------------------------------

function stepStates(run) {
  const done = {
    author: run.author_text !== null, check: run.tests_1 !== null, critic: run.review !== null,
    resolve: run.resolution !== null, recheck: run.tests_2 !== null,
  };
  return STAGES.map(([key, label]) => ({
    label, state: done[key] ? 'done' : run.status === 'running' && run.stage === key ? 'now' : 'todo',
  }));
}

function renderTopbar() {
  const run = view.runId ? view.run : null;
  const title = run ? (run.task.trim().split('\n')[0] || run.id) : repo() ? 'Nova tarefa' : 'Dual AI';
  const titleRow = h('div', { class: 'title-row' }, h('h1', { class: 'title', text: title }));
  if (run) {
    const [label, kind] = STATUS[run.status] || [run.status, 'b-neutral'];
    titleRow.append(badge(label, kind));
    titleRow.append(badge(run.fake ? 'Ensaio, sem modelos' : 'Modo real · consome sua franquia', run.fake ? 'b-neutral' : 'b-warn'));
  } else if (repo()) {
    titleRow.append(badge(view.fake ? 'Ensaio, sem modelos' : 'Modo real · consome sua franquia', view.fake ? 'b-neutral' : 'b-warn'));
  }
  const text = h('div', { class: 'topbar-text' }, titleRow);
  if (run) {
    const steps = h('div', { class: 'steps', role: 'list', 'aria-label': 'Etapas da execução' });
    for (const step of stepStates(run)) {
      const mark = step.state === 'done' ? icon('check', 13) : h('span', { class: 'ring', 'aria-hidden': 'true' });
      const spoken = { done: 'concluída', now: 'em andamento', todo: 'não executada' }[step.state];
      steps.append(h('span', { class: `step ${step.state}`, role: 'listitem', 'aria-label': `${step.label}: ${spoken}` },
        mark, step.label));
    }
    const seconds = run.seconds ? ` · ${Math.round(run.seconds)} s` : '';
    steps.append(h('span', { class: 'meta', text: `${plural(run.calls_used, 'chamada', 'chamadas')}${seconds}` }));
    text.append(steps);
  }
  $('topbar').replaceChildren(h('div', { class: 'topbar-inner' },
    h('button', { class: 'icon-btn menu-btn', type: 'button', 'aria-label': 'Abrir o histórico', onclick: openNav }, icon('menu', 20)),
    text));
  document.title = run ? `${title} · Dual AI` : 'Dual AI';
}

// --- conversa ----------------------------------------------------------------

function message(kind, name, role, ...body) {
  return h('article', { class: `msg msg-${kind}` },
    h('header', { class: 'msg-head' }, name, h('span', { class: 'role', text: `· ${role}` })),
    h('div', { class: 'msg-body' }, body));
}

function pending(kind, name, role, label) {
  return message(kind, name, role, h('div', { class: 'pending' }, h('span', { class: 'dot', 'aria-hidden': 'true' }), label));
}

function authorMessage(run) {
  const body = [markdown(run.author_text.trim() || 'O autor não deixou mensagem.')];
  if (run.files.length) body.push(h('div', { class: 'chips' }, run.files.map((file) => h('span', { class: 'chip', text: file }))));
  if (run.diff) body.push(fold('diff', `Ver diff (${plural(run.files.length, 'arquivo', 'arquivos')})`, () => diffBlock(run.diff)));
  return message('author', authorName(run), 'Autor', body);
}

function testsMessage(run, round) {
  const passed = round === 1 ? run.tests_1_pass : run.tests_pass;
  const output = round === 1 ? run.tests_1 : run.tests_2;
  const verdict = passed === true ? h('span', { class: 'ok-text', text: 'Testes passaram.' })
    : passed === false ? h('span', { class: 'danger-text', text: 'Testes falharam.' })
      : h('span', { text: 'Testes executados.' });
  const line = h('div', {}, verdict, ' ', h('span', { class: 'muted', text: round === 2 ? 'Depois da resolução. ' : '' }),
    run.test_cmd ? h('span', { class: 'chip', text: run.test_cmd }) : null);
  return message('orch', 'Dual AI', 'Orquestrador', line,
    output && output.trim() ? fold(`tests-${round}`, 'Ver saída dos testes', () => h('pre', { text: output })) : null);
}

function finding(item) {
  const place = item.file ? `${item.file}${item.line !== null && item.line !== undefined ? `:${item.line}` : ''}` : '';
  return h('div', { class: 'finding' },
    h('div', { class: 'finding-top' }, badge(item.severity, SEVERITY[item.severity] || 'b-neutral'),
      place ? h('span', { class: 'loc', text: place }) : null,
      item.demoted_from ? h('span', { class: 'muted', text: `rebaixado de ${item.demoted_from}: faltou arquivo, linha ou cenário` }) : null),
    h('div', { class: 'finding-title', text: `${item.id} · ${item.title}` }),
    item.scenario ? h('div', { class: 'muted', text: item.scenario }) : null,
    item.suggestion ? h('div', { class: 'muted', text: `Sugestão: ${item.suggestion}` }) : null);
}

function reviewMessage(run) {
  const { blocking, backlog } = run.review;
  const body = [];
  if (blocking.length) body.push(h('div', { class: 'findings' }, blocking.map(finding)));
  else body.push(h('p', { text: 'Nenhum achado que bloqueie a conclusão.' }));
  if (backlog.length) {
    body.push(fold('backlog', `${plural(backlog.length, 'item que não bloqueia', 'itens que não bloqueiam')}`,
      () => h('div', { class: 'findings' }, backlog.map(finding))));
  }
  return message('critic', criticName(run), 'Revisor', body);
}

function resolutionMessage(run) {
  const titles = Object.fromEntries(((run.review && run.review.blocking) || []).map((item) => [item.id, item.title]));
  const rows = run.resolution.resolutions.map((item) => {
    const [label, kind] = RESOLUTION[item.state] || [item.state, 'b-neutral'];
    const title = titles[item.finding_id] || (item.finding_id === 'F0' ? 'Testes que falhavam' : '');
    return h('div', { class: 'finding' },
      h('div', { class: 'finding-top' }, badge(label, kind),
        h('span', { class: 'finding-title', text: `${item.finding_id}${title ? ` · ${title}` : ''}` })),
      item.note ? h('div', { class: 'muted', text: item.note }) : null);
  });
  return message('author', authorName(run), 'Autor', h('div', { class: 'findings' }, rows));
}

function resultCard(run) {
  const heading = {
    done: 'Pronta para a sua decisão',
    needs_decision: 'Precisa da sua decisão',
    limit_reached: 'Parou no limite de uso do provedor',
    failed: 'A execução falhou',
    cancelled: 'Execução interrompida',
    interrupted: 'Execução interrompida quando o aplicativo foi fechado',
  }[run.status] || run.status;
  const [label, kind] = STATUS[run.status] || [run.status, 'b-neutral'];
  const card = h('section', { class: 'result', 'aria-label': 'Resultado da execução' },
    h('div', { class: 'result-head' }, badge(label, kind), h('h3', { text: heading })));

  const facts = [];
  if (run.files.length) facts.push(plural(run.files.length, 'arquivo alterado', 'arquivos alterados'));
  if (run.tests_pass === true) facts.push('testes passam');
  if (run.tests_pass === false) facts.push('testes falham');
  if (run.review) {
    const states = (run.resolution ? run.resolution.resolutions : []).reduce(
      (count, item) => ({ ...count, [item.state]: (count[item.state] || 0) + 1 }), {});
    facts.push(plural(run.review.blocking.length, 'achado bloqueante', 'achados bloqueantes'));
    if (states.resolved) facts.push(plural(states.resolved, 'resolvido', 'resolvidos'));
    if (states.justified) facts.push(plural(states.justified, 'justificado', 'justificados'));
    if (states.escalated) facts.push(plural(states.escalated, 'encaminhado a você', 'encaminhados a você'));
    if (run.review.backlog.length) facts.push(plural(run.review.backlog.length, 'item não bloqueante', 'itens não bloqueantes'));
  }
  if (facts.length) card.append(h('p', { text: `${facts.join(' · ')}.` }));

  const reason = REASONS[run.reason] || (['failed', 'limit_reached'].includes(run.status) ? run.reason : '');
  if (reason) card.append(h('p', { text: reason }));
  if (run.status === 'needs_decision' && run.tests_pass === false) {
    card.append(h('p', { text: 'Os testes ainda falham. Leia a saída antes de aprovar.' }));
  }
  if (run.status === 'interrupted' && run.recovery_required) {
    card.append(h('p', { text: 'O registro não confirmou o encerramento. Confirme que não há outro Dual AI usando esta execução antes de recuperar e limpar a cópia.' }));
  } else if (run.status === 'cancelled' || run.status === 'interrupted') {
    card.append(h('p', { text: 'O que já tinha sido escrito ficou na cópia isolada. Uma execução interrompida não pode ser aprovada, só descartada.' }));
  }
  if (run.fake && run.can_decide) {
    card.append(h('p', { text: 'Isto foi um ensaio com agentes falsos. Aprovar leva o arquivo de exemplo DUALAI_FAKE.txt ao seu branch.' }));
  }

  if (run.decision === 'approved') card.append(h('p', { text: 'Aprovada. As alterações estão no seu branch.' }));
  else if (run.decision === 'discarded') card.append(h('p', { text: 'Descartada. O seu branch não foi alterado.' }));
  else card.append(h('p', { text: 'O seu branch ainda não foi alterado.' }));

  const actions = h('div', { class: 'actions' });
  if (run.can_decide) actions.append(h('button', { class: 'btn btn-primary', type: 'button', onclick: () => decide('approve') }, 'Aprovar alterações'));
  if (run.can_discard) actions.append(h('button', { class: 'btn', type: 'button', onclick: () => decide('discard') }, 'Descartar'));
  if (actions.hasChildNodes()) card.append(actions);
  return card;
}

function conversation(run) {
  const running = run.status === 'running';
  const author = [authorName(run), 'Autor'];
  const nodes = [h('div', { class: 'msg-user', text: run.task })];
  if (run.uncommitted_ignored) {
    nodes.push(h('p', { class: 'note', text: 'Havia alterações não commitadas no repositório. Elas não entraram: a execução partiu do último commit.' }));
  }
  if (run.author_text !== null) nodes.push(authorMessage(run));
  else if (running && (run.stage === 'author' || !run.stage)) nodes.push(pending('author', ...author, stageNow(run)));

  if (run.tests_1 !== null) nodes.push(testsMessage(run, 1));
  else if (running && run.stage === 'check') nodes.push(pending('orch', 'Dual AI', 'Orquestrador', stageNow(run)));

  if (run.review) nodes.push(reviewMessage(run));
  else if (running && run.stage === 'critic') nodes.push(pending('critic', criticName(run), 'Revisor', stageNow(run)));

  if (run.resolution) nodes.push(resolutionMessage(run));
  else if (running && run.stage === 'resolve') nodes.push(pending('author', ...author, stageNow(run)));

  if (run.tests_2 !== null) nodes.push(testsMessage(run, 2));
  else if (running && run.stage === 'recheck') nodes.push(pending('orch', 'Dual AI', 'Orquestrador', stageNow(run)));

  if (!running) nodes.push(resultCard(run));
  return nodes;
}

function hero(...children) {
  return h('div', { class: 'hero' }, h('span', { class: 'mark mark-lg', 'aria-hidden': 'true' }, h('span'), h('span')), children);
}

function renderConversation() {
  const scroll = $('scroll');
  const nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 120;
  const box = $('conversation');
  if (!repo()) {
    box.replaceChildren(hero(
      h('h1', { text: 'Dois modelos, uma tarefa, nenhuma cópia manual' }),
      h('p', { text: 'Claude escreve, os seus testes rodam, Codex revisa uma vez e Claude resolve o que bloquear. Para começar, escolha a pasta de um repositório git.' }),
      h('button', { class: 'btn btn-primary', type: 'button', onclick: openSettings }, 'Escolher repositório')));
  } else if (!view.runId) {
    box.replaceChildren(hero(
      h('h1', { text: `O que vamos fazer em ${repo().name}?` }),
      h('p', { text: 'Descreva uma tarefa de código. O trabalho acontece em uma cópia isolada do repositório, e nada chega ao seu branch sem a sua aprovação.' }),
      h('p', { text: view.fake ? 'Modo de ensaio ativo: os agentes são falsos. O comando de testes roda de verdade.' : 'Modo real: Claude e Codex consomem a franquia dos seus planos ou geram custos de API, conforme a autenticação.' })));
  } else if (view.run) {
    box.replaceChildren(...conversation(view.run));
  } else {
    box.replaceChildren(h('p', { class: 'note', text: 'Carregando a conversa…' }));
  }
  if (nearBottom) scroll.scrollTop = scroll.scrollHeight;
}

// --- rodapé ------------------------------------------------------------------

function composer() {
  const area = h('textarea', {
    id: 'task', rows: '2', 'aria-label': 'Tarefa de código', 'aria-describedby': 'task-help',
    placeholder: 'Descreva uma tarefa de código para este repositório',
  });
  area.value = prefs.get('draft', '');
  const grow = () => { area.style.height = 'auto'; area.style.height = `${Math.min(area.scrollHeight, 240)}px`; };
  area.addEventListener('input', () => { prefs.set('draft', area.value); grow(); setError(''); });
  area.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); submit(); }
  });
  queueMicrotask(grow);
  return h('div', { class: 'composer' }, area,
    h('div', { class: 'composer-foot' },
      h('span', { class: 'where', title: repo().path }, icon('folder', 14), h('span', { text: repo().name })),
      h('span', { class: 'where cmd', title: `Comando de testes: ${repo().test_cmd}` }, icon('terminal', 14), h('span', { text: repo().test_cmd })),
      h('span', { class: 'keys', id: 'task-help', text: 'Enter envia · Shift+Enter quebra a linha' }),
      h('button', { class: 'send', id: 'send', type: 'button', 'aria-label': 'Enviar tarefa', onclick: submit }, icon('send', 18))));
}

function bar(text, small, ...buttons) {
  return h('div', { class: 'bar' }, h('div', { class: 'grow' }, text, small ? h('small', { text: small }) : null), buttons);
}

function renderDock() {
  const run = view.runId ? view.run : null;
  const active = view.state && view.state.active;
  let key = 'none';
  if (!repo()) key = 'none';
  else if (view.runId && !run) key = 'loading';
  else if (run && run.status === 'running') key = `running:${run.stage}:${run.interrupting}`;
  else if (run) key = 'finished';
  else if (active) key = `active:${active.repo}:${active.run_id}:${!!active.interrupting}:${view.error}`;
  else if (!repo().test_cmd) key = 'needs-tests';
  else key = `composer:${repo().path}:${repo().test_cmd}`;
  if (key === view.dockKey) return;
  view.dockKey = key;

  const dock = $('dock');
  if (key === 'none' || key === 'loading') {
    dock.replaceChildren();
  } else if (key.startsWith('running')) {
    const stop = run.interrupting ? null
      : h('button', { class: 'btn', type: 'button', onclick: interrupt }, icon('stop', 15), 'Interromper');
    dock.replaceChildren(bar(
      h('div', { class: 'now', role: 'status' }, h('span', { class: 'dot', 'aria-hidden': 'true' }), stageNow(run)),
      'Fechar esta aba não interrompe a execução.', stop));
  } else if (key === 'finished') {
    dock.replaceChildren(bar(h('span', { class: 'muted', text: 'Cada conversa é uma tarefa.' }), '',
      h('button', { class: 'btn', type: 'button', onclick: newTask }, icon('plus', 15), 'Nova tarefa')));
  } else if (key.startsWith('active')) {
    const label = active.run_id ? 'Há uma execução em andamento.'
      : active.interrupting ? 'Cancelando a preparação…' : 'Preparando a execução…';
    const action = active.run_id
      ? h('button', { class: 'btn', type: 'button', onclick: () => openRun(active.run_id, active.repo) }, 'Abrir')
      : active.interrupting ? null : h('button', { class: 'btn', type: 'button', onclick: interrupt }, 'Interromper');
    dock.replaceChildren(bar(h('span', { text: label }), 'Uma tarefa por vez.', action));
    if (view.error) dock.append(h('p', { class: 'error', role: 'alert', text: view.error }));
  } else if (key === 'needs-tests') {
    dock.replaceChildren(bar(h('span', { text: 'Defina o comando de testes deste repositório para enviar tarefas.' }), '',
      h('button', { class: 'btn btn-primary', type: 'button', onclick: openSettings }, 'Configurar')));
  } else {
    dock.replaceChildren(composer(), h('p', { class: 'error', id: 'dock-error', role: 'alert', hidden: true }));
    setError(view.error);
  }
}

function setError(message) {
  view.error = message;
  const node = $('dock-error');
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function render() {
  renderSidebar();
  renderTopbar();
  renderConversation();
  renderDock();
}

// --- dados -------------------------------------------------------------------

async function refreshState() {
  const navigation = view.navigation;
  const state = await api('GET', '/api/state');
  if (navigation !== view.navigation) return;
  // A seleção feita nesta página não deve saltar por causa de outra aba.
  const selected = repo();
  if (selected) {
    const entry = [state.current, ...state.recent].find((item) => item && item.path === selected.path);
    state.current = entry || selected;
  }
  view.state = state;
}

async function refreshRuns() {
  const repoPath = repo() && repo().path;
  const runs = repoPath ? (await api('GET', `/api/runs?repo=${encodeURIComponent(repoPath)}`)).runs : [];
  if ((repo() && repo().path) === repoPath) view.runs = runs;
}

async function loadRun(runId, repoPath = view.runRepo) {
  const detail = await api('GET', `/api/runs/${encodeURIComponent(runId)}?repo=${encodeURIComponent(repoPath)}`);
  if (view.runId !== runId || view.runRepo !== repoPath || !repo() || repo().path !== repoPath) return;
  const before = view.run ? view.run.status : null;
  const json = JSON.stringify(detail);
  const changed = json !== view.runJson;
  view.run = detail;
  view.runJson = json;
  if (before === 'running' && detail.status !== 'running') {
    await refreshState();
    await refreshRuns();
    if (view.runId === runId && view.runRepo === repoPath) render();
  } else if (changed) {
    renderTopbar();
    renderConversation();
    renderDock();
  }
}

function schedule() {
  clearTimeout(view.timer);
  view.timer = null;
  if (view.busy || (view.state && view.state.active) || (view.runId && view.run && view.run.status === 'running')) {
    view.timer = setTimeout(async () => {
      try {
        const previous = JSON.stringify(view.state && view.state.active);
        await refreshState();
        if (view.runId && view.runRepo) await loadRun(view.runId, view.runRepo);
        if (previous !== JSON.stringify(view.state && view.state.active)) {
          await refreshRuns();
          renderSidebar();
        }
        renderDock();
      } catch (error) { toast(error.message); }
      schedule();
    }, 1000);
  }
}

async function openRun(runId, repoPath = repo() && repo().path) {
  if (!runId || !repoPath) return;
  const navigation = ++view.navigation;
  view.runId = runId;
  view.runRepo = repoPath;
  view.run = null;
  view.runJson = '';
  view.open.clear();
  try {
    if (!repo() || repo().path !== repoPath) {
      const state = await api('POST', '/api/repos', { path: repoPath });
      if (view.navigation !== navigation) return;
      view.state = state;
      await refreshRuns();
      if (view.navigation !== navigation) return;
    }
    render();
    await loadRun(runId, repoPath);
  } catch (error) {
    if (view.navigation !== navigation) return;
    toast(error.message);
    view.runId = null;
    view.runRepo = null;
    render();
    schedule();
    return;
  }
  if (view.navigation !== navigation) return;
  renderSidebar();
  $('scroll').scrollTop = $('scroll').scrollHeight;
  schedule();
}

function newTask() {
  ++view.navigation;
  view.runId = null;
  view.runRepo = null;
  view.run = null;
  view.runJson = '';
  closeNav();
  render();
  schedule();
  const area = $('task');
  if (area) area.focus();
}

// --- ações -------------------------------------------------------------------

async function submit() {
  if (view.busy) return;
  const area = $('task');
  const task = area ? area.value.trim() : '';
  if (!task) { setError('Escreva a tarefa antes de enviar.'); if (area) area.focus(); return; }
  view.busy = true;
  const submittedRepo = repo().path;
  schedule();
  $('send').setAttribute('aria-disabled', 'true');
  setError('');
  try {
    const started = await api('POST', '/api/runs', { repo: submittedRepo, task, fake: view.fake });
    prefs.set('draft', '');
    await refreshState();
    await refreshRuns();
    await openRun(started.run_id, submittedRepo);
  } catch (error) {
    setError(error.message);
    try { await refreshState(); } catch { /* conserva o erro de início */ }
    view.dockKey = '';
    renderDock();
    const button = $('send');
    if (button) button.removeAttribute('aria-disabled');
  } finally {
    view.busy = false;
    schedule();
  }
}

async function interrupt() {
  try {
    await api('POST', '/api/runs/interrupt', {});
    await refreshState();
    if (view.runId && view.runRepo) await loadRun(view.runId, view.runRepo);
    renderDock();
    schedule();
  } catch (error) {
    toast(error.message);
  }
}

function confirmDialog(title, text, okLabel) {
  const dialog = $('confirm');
  $('confirm-title').textContent = title;
  $('confirm-text').textContent = text;
  $('confirm-ok').textContent = okLabel;
  dialog.returnValue = '';
  dialog.showModal();
  $('confirm-cancel').focus();
  return new Promise((resolve) => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'ok'), { once: true });
  });
}

async function decide(kind) {
  const run = view.run;
  const approved = kind === 'approve';
  const confirmed = await confirmDialog(
    approved ? 'Aprovar as alterações?' : 'Descartar as alterações?',
    approved
      ? `As alterações desta execução entram no branch atual de ${repo().name} em um commit, com uma linha em docs/decisions.md. Se o branch mudou desde o início, nada é alterado e você é avisado.`
      : 'A cópia isolada desta execução é apagada e o seu branch não é alterado. A conversa continua no histórico.',
    approved ? 'Aprovar alterações' : 'Descartar');
  if (!confirmed || view.run !== run) return;
  try {
    const detail = await api('POST', `/api/runs/${encodeURIComponent(run.id)}/${kind}`, { repo: repo().path });
    view.run = detail;
    view.runJson = JSON.stringify(detail);
    await refreshRuns();
    render();
    toast(approved ? 'Aprovada. As alterações estão no seu branch.' : 'Descartada. O seu branch não foi alterado.');
  } catch (error) {
    toast(error.message);
  }
}

// --- configurações -----------------------------------------------------------

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  prefs.set('theme', theme);
}

function fillSettings() {
  const current = repo();
  $('repo-path').textContent = current ? current.path : 'Nenhum repositório selecionado.';
  $('test-cmd').value = current ? current.test_cmd : '';
  $('test-cmd').disabled = !current;
  $('test-saved').textContent = '';
  for (const id of ['repo-error', 'test-error']) { $(id).hidden = true; $(id).textContent = ''; }
  $('fake-toggle').checked = view.fake;
  for (const radio of document.querySelectorAll('input[name="theme"]')) {
    radio.checked = radio.value === document.documentElement.dataset.theme;
  }
  const others = ((view.state && view.state.recent) || []).filter((item) => !current || item.path !== current.path);
  const recent = others.map((item) => h('button', { type: 'button', onclick: () => useRepo(item.path) },
    h('span', { text: item.name }), h('span', { class: 'path', text: item.path })));
  if (recent.length) recent.unshift(h('div', { class: 'recent-title', text: 'Recentes' }));
  $('repo-recent').replaceChildren(...recent);
}

function openSettings() {
  closeNav();
  fillSettings();
  $('settings').showModal();
}

function showFieldError(id, message) {
  $(id).textContent = message;
  $(id).hidden = false;
}

async function afterRepoChange(state) {
  ++view.navigation;
  view.state = state;
  view.runId = null;
  view.runRepo = null;
  view.run = null;
  view.dockKey = '';
  await refreshRuns();
  fillSettings();
  render();
  schedule();
}

async function useRepo(path) {
  try {
    await afterRepoChange(await api('POST', '/api/repos', { path }));
    $('repo-input').value = '';
  } catch (error) {
    showFieldError('repo-error', error.message);
  }
}

async function pickRepo() {
  const button = $('repo-pick');
  button.setAttribute('aria-disabled', 'true');
  button.textContent = 'Escolha a pasta na janela que abriu…';
  try {
    const state = await api('POST', '/api/repos/pick', {});
    if (state.picked) await afterRepoChange(state);
  } catch (error) {
    showFieldError('repo-error', error.message);
  } finally {
    button.removeAttribute('aria-disabled');
    button.textContent = 'Escolher pasta…';
  }
}

async function saveTestCommand() {
  if (!repo()) { showFieldError('test-error', 'Escolha um repositório primeiro.'); return; }
  try {
    view.state = await api('POST', '/api/repos/settings', { repo: repo().path, test_cmd: $('test-cmd').value });
    view.dockKey = '';
    $('test-error').hidden = true;
    $('test-saved').textContent = 'Comando salvo.';
    render();
  } catch (error) {
    showFieldError('test-error', error.message);
  }
}

// --- navegação em telas estreitas --------------------------------------------

function openNav() { $('app').classList.add('nav-open'); $('scrim').hidden = false; $('new-task').focus(); }
function closeNav() { $('app').classList.remove('nav-open'); $('scrim').hidden = true; }

// --- início ------------------------------------------------------------------

function wire() {
  $('new-task').prepend(icon('plus', 15));
  $('new-task').addEventListener('click', newTask);
  $('repo-button').addEventListener('click', openSettings);
  $('settings-button').addEventListener('click', openSettings);
  $('scrim').addEventListener('click', closeNav);
  $('settings-close').append(icon('x', 18));
  $('settings-close').addEventListener('click', () => $('settings').close());
  $('repo-pick').addEventListener('click', () => { if (!$('repo-pick').hasAttribute('aria-disabled')) pickRepo(); });
  $('repo-use').addEventListener('click', () => useRepo($('repo-input').value));
  $('repo-input').addEventListener('keydown', (event) => { if (event.key === 'Enter') useRepo($('repo-input').value); });
  $('test-save').addEventListener('click', saveTestCommand);
  $('test-cmd').addEventListener('keydown', (event) => { if (event.key === 'Enter') saveTestCommand(); });
  $('test-cmd').addEventListener('input', () => { $('test-saved').textContent = ''; });
  $('fake-toggle').addEventListener('change', (event) => {
    view.fake = event.target.checked;
    prefs.set('fake', view.fake);
    render();
  });
  for (const radio of document.querySelectorAll('input[name="theme"]')) {
    radio.addEventListener('change', () => applyTheme(radio.value));
  }
  $('confirm-cancel').addEventListener('click', () => $('confirm').close('cancel'));
  $('confirm-ok').addEventListener('click', () => $('confirm').close('ok'));
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeNav(); });
}

async function boot() {
  applyTheme(prefs.get('theme', 'dark'));
  wire();
  try {
    await refreshState();
    await refreshRuns();
  } catch (error) {
    $('conversation').replaceChildren(hero(h('h1', { text: 'Não foi possível falar com o Dual AI' }), h('p', { text: error.message })));
    return;
  }
  render();
  if (view.state.active && repo() && view.state.active.repo === repo().path && view.state.active.run_id) await openRun(view.state.active.run_id, view.state.active.repo);
  schedule();
}

boot();
