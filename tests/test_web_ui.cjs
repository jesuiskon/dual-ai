// Regressões de navegação sem navegador, rede ou modelos reais.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/dualai/web/static/app.js'), 'utf8');

function page(saved = {}) {
  const nodes = new Map();
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {}; this.events = {};
      this.style = {}; this.scrollHeight = 100; this.scrollTop = 0; this.clientHeight = 100;
      this.value = ''; this.textContent = ''; this.classList = { add() {}, remove() {} };
    }
    setAttribute(key, value) { this.attributes[key] = value; if (key === 'id') nodes.set(value, this); }
    removeAttribute(key) { delete this.attributes[key]; }
    hasAttribute(key) { return key in this.attributes; }
    addEventListener(key, fn) { (this.events[key] ||= []).push(fn); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    hasChildNodes() { return this.children.length > 0; }
    focus() {}
  }
  for (const id of ['dock', 'scroll', 'toast', 'task', 'send', 'topbar']) nodes.set(id, new Element());
  const storage = new Map(Object.entries(saved));
  const timers = new Map(); let sequence = 0;
  const context = vm.createContext({
    document: {
      querySelector: () => ({ content: 'synthetic-session' }),
      getElementById: (id) => nodes.get(id) || null,
      createElement: (tag) => new Element(tag), createElementNS: (_, tag) => new Element(tag),
    },
    localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
    setTimeout: (fn) => { const id = ++sequence; timers.set(id, fn); return id; },
    clearTimeout: (id) => timers.delete(id), queueMicrotask: (fn) => fn(),
  });
  vm.runInContext(source.replace(/\nboot\(\);\s*$/, '\n'), context);
  const originals = {};
  for (const name of ['render', 'renderSidebar', 'renderTopbar', 'renderConversation', 'fillSettings', 'closeNav', 'toast']) {
    originals[name] = context[name]; context[name] = () => {};
  }
  const view = vm.runInContext('view', context);
  return {
    context, view, nodes, timers, originals,
    async tick() {
      const [id, fn] = timers.entries().next().value;
      timers.delete(id); await fn();
    },
  };
}
const repo = (name) => ({ path: `/repo-${name}`, name, test_cmd: 'synthetic tests' });
const state = (current, active = null) => ({ current, recent: [repo('A'), repo('B')], active });
const content = (node) => [node.textContent || '', ...(node.children || []).map((child) => typeof child === 'string' ? child : content(child))].join(' ');


test('first use rehearses, while an explicit real-mode preference survives', () => {
  assert.equal(page().view.fake, true);
  assert.equal(page({ 'dualai.fake': 'false' }).view.fake, false);
  assert.equal(page({ 'dualai.fake': 'true' }).view.fake, true);
});

test('new task keeps polling the active run and releases the composer when it ends', async () => {
  const p = page();
  p.view.state = state(repo('A'), { repo: '/repo-A', run_id: 'running-A' });
  p.view.runId = 'running-A'; p.view.runRepo = '/repo-A'; p.view.run = { status: 'running' };
  const calls = [];
  p.context.api = async (method, url) => {
    calls.push(url);
    if (url === '/api/state') return state(repo('A'));
    if (url.startsWith('/api/runs?')) return { runs: [] };
    throw new Error(`unexpected ${method} ${url}`);
  };
  p.context.newTask();
  assert.equal(p.view.runId, null);
  assert.equal(p.timers.size, 1);
  await p.tick();
  assert.equal(p.view.state.active, null);
  assert.match(p.view.dockKey, /^composer:/);
  assert.equal(p.timers.size, 0);
  assert.ok(calls.includes('/api/state'));
});

test('opening the active run selects its repository before loading its detail', async () => {
  const p = page();
  p.view.state = state(repo('B'), { repo: '/repo-A', run_id: 'running-A' });
  const calls = [];
  p.context.api = async (method, url, body) => {
    calls.push({ method, url, body });
    if (method === 'POST' && url === '/api/repos') return state(repo('A'), { repo: '/repo-A', run_id: 'running-A' });
    if (url.startsWith('/api/runs?')) return { runs: [] };
    if (url === '/api/runs/running-A?repo=%2Frepo-A') return { id: 'running-A', status: 'done' };
    throw new Error(`unexpected ${method} ${url}`);
  };
  await p.context.openRun('running-A', '/repo-A');
  assert.equal(calls[0].body.path, '/repo-A');
  assert.equal(p.view.state.current.path, '/repo-A');
  assert.equal(p.view.runRepo, '/repo-A');
  assert.ok(calls.some(({ url }) => url === '/api/runs/running-A?repo=%2Frepo-A'));
  assert.ok(!calls.some(({ url }) => url.includes('running-A?repo=%2Frepo-B')));
});

test('late detail for the same run id in another repository is ignored', async () => {
  const p = page();
  p.view.state = state(repo('A')); p.view.runId = 'same-id'; p.view.runRepo = '/repo-A';
  let resolve;
  p.context.api = () => new Promise((done) => { resolve = done; });
  const loading = p.context.loadRun('same-id', '/repo-A');
  p.view.state = state(repo('B')); p.view.runRepo = '/repo-B';
  const current = { id: 'same-id', task: 'B task' }; p.view.run = current;
  resolve({ id: 'same-id', task: 'A task', status: 'done' });
  await loading;
  assert.equal(p.view.run, current);
});

test('preparation without a run id is cancellable and has no invalid open button', () => {
  const p = page();
  p.view.state = state(repo('A'), { repo: '/repo-A', run_id: null, status: 'preparing', interrupting: false });
  p.context.renderDock();
  const text = content(p.nodes.get('dock'));
  assert.match(text, /Preparando a execução/);
  assert.match(text, /Interromper/);
  assert.doesNotMatch(text, /Abrir/);
  p.view.state.active.interrupting = true;
  p.context.renderDock();
  assert.match(content(p.nodes.get('dock')), /Cancelando a preparação/);
});

test('start timeout refreshes preparation state and retains polling until cancellation completes', async () => {
  const p = page();
  p.view.state = state(repo('A')); p.nodes.get('task').value = 'Synthetic task';
  const calls = [];
  p.context.api = async (method, url) => {
    calls.push(url);
    if (method === 'POST' && url === '/api/runs') throw new Error('Início demorou; cancelamento solicitado.');
    if (url === '/api/state') return state(repo('A'), { repo: '/repo-A', run_id: null, status: 'preparing', interrupting: true });
    throw new Error(`unexpected ${method} ${url}`);
  };
  await p.context.submit();
  assert.equal(p.view.busy, false);
  assert.equal(p.view.state.active.interrupting, true);
  assert.equal(p.timers.size, 1);
  assert.ok(calls.includes('/api/state'));
  assert.match(content(p.nodes.get('dock')), /Cancelando a preparação/);
  assert.match(content(p.nodes.get('dock')), /cancelamento solicitado/);
});


test('an unconfirmed interrupted run explains recovery without offering discard', () => {
  const p = page();
  const card = p.context.resultCard({ status: 'interrupted', recovery_required: true, files: [], can_decide: false, can_discard: false });
  const text = content(card);
  assert.match(text, /O registro não confirmou o encerramento/);
  assert.doesNotMatch(text, /só descartada/);
  assert.doesNotMatch(text, /Descartar/);
});


test('an open conversation labels real calls distinctly from rehearsal', () => {
  const p = page();
  p.view.state = state(repo('A')); p.view.runId = 'synthetic-run';
  p.view.run = { id: 'synthetic-run', task: 'Synthetic task', status: 'done', fake: false, calls_used: 3 };
  p.originals.renderTopbar();
  assert.match(content(p.nodes.get('topbar')), /Modo real · consome sua franquia/);
  p.view.run.fake = true;
  p.originals.renderTopbar();
  assert.match(content(p.nodes.get('topbar')), /Ensaio, sem modelos/);
  assert.doesNotMatch(content(p.nodes.get('topbar')), /Modo real/);
});
