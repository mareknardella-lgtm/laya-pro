import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const appSource = readFileSync(new URL('../app.js', import.meta.url), 'utf8');
const htmlSource = readFileSync(new URL('../index.html', import.meta.url), 'utf8');

class Element {
  constructor(id = '', documentRef = null, attributes = {}) {
    this.id = id;
    this.documentRef = documentRef;
    this.dataset = attributes;
    this.value = '';
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
    this.open = false;
    this.returnValue = '';
    this.className = '';
    this.attributes = {};
    this.listeners = new Map();
    this.children = [];
    this._innerHTML = '';
    this.classList = {
      add: (name) => this._toggleClass(name, true),
      remove: (name) => this._toggleClass(name, false),
      toggle: (name, force) => {
        const shouldAdd = force === undefined ? !this.className.split(/\s+/).includes(name) : Boolean(force);
        this._toggleClass(name, shouldAdd);
        return shouldAdd;
      },
      contains: (name) => this.className.split(/\s+/).includes(name),
    };
  }

  _toggleClass(name, add) {
    const classes = new Set(this.className.split(/\s+/).filter(Boolean));
    if (add) classes.add(name); else classes.delete(name);
    this.className = [...classes].join(' ');
  }

  insertAdjacentHTML(_position, value) { this._innerHTML += String(value); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, callback, options = {}) {
    const callbacks = this.listeners.get(name) || [];
    callbacks.push(callback);
    this.listeners.set(name, callbacks);
    if (options?.signal) {
      const remove = () => {
        const current = this.listeners.get(name) || [];
        this.listeners.set(name, current.filter((item) => item !== callback));
      };
      if (options.signal.aborted) remove();
      else options.signal.addEventListener('abort', remove, { once: true });
    }
  }
  set innerHTML(value) {
    this._innerHTML = String(value);
    if (this.id === 'project-selector') {
      const first = this._innerHTML.match(/<option value="([^"]*)"/)?.[1] || '';
      this.allowedValues = new Set([...this._innerHTML.matchAll(/<option value="([^"]*)"/g)].map((match) => match[1]));
      this.value = this.allowedValues.has(this.value) ? this.value : first;
    }
  }
  get innerHTML() { return this._innerHTML; }
  async dispatch(name, properties = {}) {
    const event = { type: name, target: this, currentTarget: this, preventDefault() {}, ...properties };
    const results = [];
    for (const callback of this.listeners.get(name) || []) results.push(callback(event));
    return Promise.all(results.map((result) => Promise.resolve(result)));
  }
  async click() {
    if (this.disabled || this.hidden) return;
    return this.dispatch('click');
  }
  focus() {}
  showModal() { this.open = true; }
  close(returnValue = '') {
    this.open = false;
    this.returnValue = returnValue;
    void this.dispatch('close');
  }
  append(child) { this.children.push(child); }
  remove() { this.removed = true; }
}

function makeResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => name.toLowerCase() === 'content-type' ? 'application/json' : null },
    json: async () => payload,
  };
}

function setupDashboard(fetchHandler) {
  const elements = new Map();
  const actionNodes = new Map();
  let document;
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, new Element(id, document));
    return elements.get(id);
  };
  const querySelector = (selector) => {
    if (selector.startsWith('.')) {
      const className = selector.slice(1);
      return getElement(className);
    }
    if (!selector.startsWith('#')) return null;
    const id = selector.slice(1);
    if (id === 'approval-detail-panel' && !getElement('page-view').innerHTML.includes('id="approval-detail-panel"')) return null;
    return getElement(id);
  };
  const querySelectorAll = (selector) => {
    if (selector === '[data-nav]') return [];
    if (selector === '[data-busy-key]') {
      return [...actionNodes.values()].filter((element) => element.dataset.busyKey);
    }
    if (selector.startsWith('[data-busy-key=')) {
      const wanted = selector.match(/data-busy-key="([^"]+)"/)?.[1];
      return [...actionNodes.values()].filter((element) => element.dataset.busyKey === wanted);
    }
    if (!selector.startsWith('[data-action]')) return [];
    const detailPanel = elements.has('approval-detail-panel') ? getElement('approval-detail-panel').innerHTML : '';
    const html = getElement('page-view').innerHTML.replace(/<\/?section\b[^>]*>/g, '') + detailPanel;
    const matches = [];
    const expression = /<(button|a)\b([^>]*\bdata-action="([^"]+)"[^>]*)>([\s\S]*?)<\/\1>/g;
    for (const match of html.matchAll(expression)) {
      const attrs = {};
      for (const attr of match[2].matchAll(/\b(data-[a-z0-9-]+)="([^"]*)"/gi)) {
        const key = attr[1].slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase());
        attrs[key] = attr[2].replaceAll('&amp;', '&').replaceAll('&quot;', '"').replaceAll('&#39;', "'");
      }
      const identity = attrs.busyKey || `${attrs.action || ''}:${attrs.approval || ''}:${attrs.workflow || ''}:${attrs.filter || ''}`;
      const actionElement = actionNodes.get(identity) || new Element('', document, attrs);
      actionElement.dataset = attrs;
      actionElement.textContent = match[4].replace(/<[^>]+>/g, '');
      actionNodes.set(identity, actionElement);
      matches.push(actionElement);
    }
    return matches;
  };
  document = {
    querySelector,
    querySelectorAll,
    createElement: () => new Element('', document),
    getElement,
  };
  const windowListeners = new Map();
  const window = {
    addEventListener: (name, callback) => {
      const callbacks = windowListeners.get(name) || [];
      callbacks.push(callback);
      windowListeners.set(name, callbacks);
    },
    dispatch: async (name) => Promise.all((windowListeners.get(name) || []).map((callback) => callback({ type: name }))),
  };

  // Minimal Web Audio API stub: lets the real alert synthesizer run so the tests can
  // observe that a critical beep was emitted (and later silenced by the operator).
  const audioBeeps = [];
  const audioParam = () => ({ setValueAtTime() {}, exponentialRampToValueAtTime() {}, value: 0 });
  const audioNode = (kind) => ({
    kind,
    gain: audioParam(),
    frequency: audioParam(),
    type: '',
    connect: (target) => target,
    start: () => {},
    stop: () => {},
  });
  class MockAudioContext {
    constructor() { this.state = 'running'; this.currentTime = 0; this.destination = { kind: 'destination' }; }
    resume() { return Promise.resolve(); }
    createOscillator() { const node = audioNode('oscillator'); audioBeeps.push(node); return node; }
    createGain() { return audioNode('gain'); }
  }
  window.AudioContext = MockAudioContext;

  // Track timers so polling loops, the repeating alert and the per-request timeout budget
  // can be asserted without firing. Long timers (>= 1s) are recorded instead of running
  // immediately, so a request only aborts when the test explicitly fires its budget.
  const timers = new Map();
  let timerId = 0;
  const fireTimers = (ms) => {
    for (const [id, timer] of [...timers]) {
      if (timer.timeout === ms) { timers.delete(id); timer.callback(); }
    }
  };
  const storage = new Map();
  const localStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
  };
  const context = {
    document,
    window,
    location: { hash: '', origin: 'http://127.0.0.1:8765' },
    localStorage,
    fetch: fetchHandler,
    Headers,
    URLSearchParams,
    AbortController,
    CSS: { escape: (value) => String(value) },
    Intl,
    Date,
    JSON,
    String,
    Number,
    Math,
    Object,
    Promise,
    setTimeout: (callback, ms = 0) => {
      if (ms >= 1000) { timerId += 1; timers.set(timerId, { callback, timeout: ms }); return timerId; }
      callback();
      return 1;
    },
    clearTimeout: (id) => { timers.delete(id); },
    setInterval: (callback, ms) => { timerId += 1; timers.set(timerId, { callback, timeout: ms }); return timerId; },
    clearInterval: (id) => { timers.delete(id); },
    console,
  };
  vm.runInNewContext(appSource, context, { filename: 'frontend/app.js' });
  return { context, document, elements, getElement, localStorage, window, audioBeeps, timers, fireTimers };
}

const waitFor = async (predicate) => {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.fail('dashboard did not reach its expected state');
};

const healthyStatus = {
  observed_at: '2026-01-01T12:00:00Z', api_status: 'healthy', last_successful_health_check: null,
  latest_audit_event_at: null, last_successful_execution_at: null, audit_event_count: 0, decision_count: 0,
  pending_approval_count: 0, active_workflow_count: 0, execution_counts: {},
  system1: { configured: true, status: 'operational', last_attempt_at: null, last_successful_at: null, last_latency_ms: null, last_error_code: null, native_runtime_verified: false },
  system2: { configured: false, status: 'unavailable', last_attempt_at: null, last_successful_at: null, last_latency_ms: null, last_error_code: null, native_runtime_verified: false },
};

function apiFetch(requests, approvalRecords = [], workflowStatus = 'awaiting_approval', workflowApprovalStatus = 'pending', approvalExpiresAt = '2030-01-01T13:00:00Z') {
  return async (url, options = {}) => {
    const parsed = new URL(url, 'http://127.0.0.1:8765');
    requests.push({ path: `${parsed.pathname}${parsed.search}`, options });
    const path = parsed.pathname;
    if (path === '/health' || path === '/api/v1/health') return makeResponse(200, { status: 'ok', service: 'laya-pro', mode: 'local' });
    if (path === '/api/v1/health/services') return makeResponse(200, {
      overall_status: 'all_operational',
      backend: { name: 'Backend FastAPI', status: 'online', endpoint: 'http://127.0.0.1:8765', latency_ms: 1.5, checked_at: '2026-01-01T12:00:00Z', message: 'Backend operativo e reattivo' },
      system1: { name: 'Laya System 1', status: 'online', endpoint: 'http://127.0.0.1:8000/v1/systemone', latency_ms: 12.4, checked_at: '2026-01-01T12:00:00Z', message: 'Laya System 1 daemon operativo' },
    });
    if (path === '/api/v1/status') return makeResponse(200, { system1_configured: true, system2_configured: false, tool_execution_available: true });
    if (path === '/api/v1/operator/session/connect' && options.method === 'POST') {
      return makeResponse(200, {
        session_token: 'laya_sess_test_operator_token_12345',
        status: 'connected',
        expires_at: '2030-01-01T00:00:00Z',
        projects: ['demo', 'other'],
      });
    }
    if (path === '/api/v1/operator/session/disconnect' && options.method === 'POST') {
      return makeResponse(200, { status: 'disconnected' });
    }
    if (path === '/api/v1/operator/status') return makeResponse(200, healthyStatus);
    if (path === '/api/v1/operator/projects') return makeResponse(200, ['demo', 'other']);
    if (path === '/api/v1/operator/approvals' && parsed.searchParams.has('limit')) return makeResponse(200, approvalRecords);
    if (path === '/api/v1/operator/approvals/approval-1') return makeResponse(200, {
      approval: {
        approval_id: 'approval-1', created_at: '2026-01-01T11:00:00Z', expires_at: '2030-01-01T13:00:00Z',
        status: 'pending', tool_id: 'file.replace', input_fingerprint: 'abcdef012345', project_id: 'demo', workflow_id: 'workflow-1',
        request_id: 'request-1', requested_by: 'operator', decided_by: null, reason: '<script>alert(1)</script>',
        parameters_preview: { path: '<img src=x onerror=alert(1)>', content: '[OMITTED]' },
      },
      validation: { status: 'valid', message: 'Fingerprint matched by backend.' },
      parameters_preview: { path: '<img src=x onerror=alert(1)>', content: '[OMITTED]' },
      operation_id: 'operation-1', associated_workflow_status: workflowStatus,
    });
    if (path.endsWith('/approve') && options.method === 'POST') return makeResponse(200, {
      approval_id: 'approval-1', status: 'approved', decided_by: 'local-operator',
    });
    if (path === '/api/v1/operator/workflows' && parsed.searchParams.has('limit')) return makeResponse(200, []);
    if (path === '/api/v1/operator/workflows/workflow-1') return makeResponse(200, {
      workflow_id: 'workflow-1', project_id: 'demo', status: workflowStatus, version: 2,
      created_at: '2026-01-01T10:00:00Z', updated_at: '2026-01-01T11:00:00Z', pending_step: workflowStatus === 'paused' || workflowStatus === 'awaiting_approval' ? 'write' : null,
      completed_steps: [], error_code: null, events: [],
      steps: [{ step_id: 'write', tool_id: 'file.replace', depends_on: [], status: workflowStatus, approval_id: 'approval-1', approval_status: workflowApprovalStatus, approval_expires_at: approvalExpiresAt }],
    });
    if (path === '/api/v1/operator/executions' && parsed.searchParams.has('limit')) return makeResponse(200, { items: [], total: 0, limit: 20, offset: 0 });
    if (path === '/api/v1/operator/executions/execution-1') return makeResponse(200, {
      execution: { execution_id: 'execution-1', request_id: 'request-1', project_id: 'demo', workflow_id: 'workflow-1', step_id: 'write', tool_id: 'file.replace', status: 'succeeded', started_at: '2026-01-01T10:00:00Z', completed_at: '2026-01-01T10:00:01Z', duration_ms: 1000, error_code: null, error_summary: null },
      workflow: null, events: [],
    });
    if (path.startsWith('/api/v1/operator/workflows/workflow-1/') && options.method === 'POST') return makeResponse(200, { status: path.split('/').at(-1) === 'resume' ? 'completed' : path.split('/').at(-1) === 'cancel' ? 'cancelled' : 'paused', version: 3, pending_step: null });
    return makeResponse(404, { detail: { code: 'not_found', message: `Unknown test route ${path}` } });
  };
}

test('dashboard opens with welcome consent screen containing explicit summary and Italian title', async () => {
  assert.match(htmlSource, /Benvenuto in Laya Pro/);
  assert.match(htmlSource, /Accetta e connetti/);
  assert.match(htmlSource, /Esci/);
  assert.match(htmlSource, /Connessione al backend locale/);
  assert.match(htmlSource, /Comunicazione con System 1/);
  assert.match(htmlSource, /Accesso alle funzionalit.* del dashboard/);
  assert.match(htmlSource, /Utilizzo delle autorizzazioni previste dal sistema/);
  assert.doesNotMatch(htmlSource, /id="operator-token"/);
  assert.doesNotMatch(htmlSource, /Verify and connect/);

  const requests = [];
  const app = setupDashboard(apiFetch(requests));
  assert.equal(app.getElement('welcome-screen').hidden, false);
  assert.equal(app.getElement('app-shell').hidden, true);
  assert.equal(app.getElement('connection-label').textContent, 'Sessione disconnessa');
});

test('operator accepts consent: connects automatically, verifies System 1, loads status and projects without manual token', async () => {
  const requests = [];
  const app = setupDashboard(apiFetch(requests));

  assert.equal(app.getElement('welcome-screen').hidden, false);
  assert.equal(app.getElement('app-shell').hidden, true);

  await app.getElement('consent-connect').click();
  await waitFor(() => app.getElement('app-shell').hidden === false);

  assert.equal(app.getElement('welcome-screen').hidden, true);
  assert.equal(app.getElement('app-shell').hidden, false);
  assert.equal(app.getElement('connection-label').textContent, 'Backend session active');

  // Verify requests were made in proper sequence
  assert.ok(requests.some((r) => r.path === '/health'));
  assert.ok(requests.some((r) => r.path === '/api/v1/status'));
  assert.ok(requests.some((r) => r.path === '/api/v1/operator/session/connect' && r.options.method === 'POST'));

  const statusRequest = requests.find((r) => r.path === '/api/v1/operator/status');
  assert.ok(statusRequest);
  assert.equal(statusRequest.options.headers.get('X-Laya-Approval-Token'), 'laya_sess_test_operator_token_12345');

  // No token is stored in localStorage or sessionStorage
  assert.equal(app.localStorage.getItem('operator-token'), null);
  assert.equal(app.localStorage.getItem('token'), null);

  assert.ok(requests.some((r) => r.path === '/api/v1/operator/projects'));
  assert.ok(requests.some((r) => r.path.startsWith('/api/v1/operator/approvals?limit=')));
  assert.ok(requests.some((r) => r.path === '/api/v1/operator/workflows?limit=8'));
  assert.ok(requests.some((r) => r.path === '/api/v1/operator/executions?limit=6&offset=0'));
  assert.equal(app.getElement('project-context').hidden, false);
  assert.match(app.getElement('project-selector').innerHTML, /All authorized projects/);
});

test('user exits consent: stops connection and displays exit confirmation with recovery option', async () => {
  const requests = [];
  const app = setupDashboard(apiFetch(requests));

  await app.getElement('consent-exit').click();
  assert.equal(app.getElement('welcome-exit-card').hidden, false);
  assert.equal(app.getElement('welcome-card').hidden, true);
  assert.equal(app.getElement('app-shell').hidden, true);
  assert.equal(requests.length, 0); // No network requests attempted

  // User clicks retry
  await app.getElement('consent-retry-exit').click();
  assert.equal(app.getElement('welcome-exit-card').hidden, true);
  assert.equal(app.getElement('welcome-card').hidden, false);
});

test('handles offline backend with clear diagnostic message and allows retry', async () => {
  const requests = [];
  const offlineFetch = async (url, options = {}) => {
    const parsed = new URL(url, 'http://127.0.0.1:8765');
    requests.push({ path: `${parsed.pathname}${parsed.search}`, options });
    throw new Error('Connection refused');
  };

  const app = setupDashboard(offlineFetch);
  await app.getElement('consent-connect').click();

  await waitFor(() => app.getElement('welcome-status-box').hidden === false);
  assert.match(app.getElement('welcome-error-detail').textContent, /Backend non raggiungibile/);
  assert.equal(app.getElement('consent-connect').disabled, false);
  assert.equal(app.getElement('app-shell').hidden, true);
});

test('disconnect button returns to welcome consent screen and revokes session', async () => {
  const requests = [];
  const app = setupDashboard(apiFetch(requests));

  await app.getElement('consent-connect').click();
  await waitFor(() => app.getElement('app-shell').hidden === false);

  await app.getElement('disconnect-button').click();
  await waitFor(() => app.getElement('welcome-screen').hidden === false);

  assert.equal(app.getElement('app-shell').hidden, true);
  assert.equal(app.getElement('connection-label').textContent, 'Sessione disconnessa');
  assert.ok(requests.some((r) => r.path === '/api/v1/operator/session/disconnect' && r.options.method === 'POST'));
});

test('project selector adds backend-authorized scope to operator requests and can be cleared', async () => {
  const requests = [];
  const app = setupDashboard(apiFetch(requests));
  await app.getElement('consent-connect').click();
  await waitFor(() => app.getElement('app-shell').hidden === false);

  const selector = app.getElement('project-selector');
  selector.value = 'demo';
  await selector.dispatch('change');
  await waitFor(() => requests.some((request) => request.path === '/api/v1/operator/status?project_id=demo'));
  assert.ok(requests.some((request) => request.path === '/api/v1/operator/approvals?limit=12&project_id=demo'));
  assert.ok(requests.some((request) => request.path === '/api/v1/operator/workflows?limit=8&project_id=demo'));
  assert.ok(requests.some((request) => request.path === '/api/v1/operator/executions?limit=6&offset=0&project_id=demo'));

  selector.value = 'not-authorized';
  await selector.dispatch('change');
  assert.equal(app.getElement('project-selector').value, '');
  assert.ok(!requests.some((request) => request.path.includes('project_id=not-authorized')));
});

test('approval detail is escaped, filtered from backend records, and duplicate decisions stay disabled until confirmation', async () => {
  const exp = '2030-01-01T00:00:00Z';
  const approvalRecords = [
    { approval: { approval_id: 'approval-1', tool_id: 'file.replace', status: 'pending', expires_at: exp, project_id: 'demo', workflow_id: 'workflow-1', parameters_preview: {} }, validation: { status: 'valid' }, parameters_preview: { path: 'notes.txt' }, operation_id: 'operation-1' },
    { approval: { approval_id: 'approval-2', tool_id: 'file.replace', status: 'approved', expires_at: exp, project_id: 'demo', workflow_id: 'workflow-1', parameters_preview: {} }, validation: { status: 'valid' }, parameters_preview: { path: 'done.txt' }, operation_id: 'operation-2' },
  ];
  const requests = [];
  const app = setupDashboard(apiFetch(requests, approvalRecords));
  await app.getElement('consent-connect').click();
  await waitFor(() => app.getElement('app-shell').hidden === false);

  app.getElement('project-selector').value = 'demo';
  await app.getElement('project-selector').dispatch('change');
  await waitFor(() => requests.some((request) => request.path === '/api/v1/operator/status?project_id=demo'));

  app.context.location.hash = '#approvals?id=approval-1';
  await app.window.dispatch('hashchange');
  await waitFor(() => app.getElement('approval-detail-panel').innerHTML.includes('Approval details') || app.getElement('page-view').innerHTML.includes('Could not load this view'));
  assert.match(app.getElement('approval-detail-panel').innerHTML, /Approval details/);
  const markup = app.getElement('page-view').innerHTML + app.getElement('approval-detail-panel').innerHTML;
  assert.match(markup, /operation-1/);
  assert.match(markup, /Fingerprint validation/);
  assert.doesNotMatch(markup, /<script>alert\(1\)<\/script>/);
  assert.doesNotMatch(markup, /<img src=x onerror=alert\(1\)>/);
  assert.match(markup, /&lt;script&gt;/);

  const approve = app.document.querySelectorAll('[data-action]').find((element) => element.dataset.action === 'approve');
  assert.ok(approve);
  const pending = approve.click();
  await waitFor(() => app.getElement('confirm-dialog').open);
  assert.equal(app.document.querySelectorAll('[data-action]').find((element) => element.dataset.action === 'approve').disabled, true);
  assert.equal(requests.filter((request) => request.path.endsWith('/approve') && request.options.method === 'POST').length, 0);
  await app.getElement('confirm-form').dispatch('submit');
  await pending;
  assert.equal(requests.filter((request) => request.path === '/api/v1/operator/approvals/approval-1/approve?project_id=demo' && request.options.method === 'POST').length, 1);
  assert.equal(requests.find((request) => request.path.endsWith('/approve?project_id=demo') && request.options.method === 'POST').options.body, undefined);
});

test('workflow detail exposes only valid server transitions and preserves pending-approval warnings', async () => {
  const requests = [];
  const app = setupDashboard(apiFetch(requests));
  await app.getElement('consent-connect').click();
  await waitFor(() => app.getElement('app-shell').hidden === false);

  app.context.location.hash = '#workflow/workflow-1';
  await app.window.dispatch('hashchange');
  await waitFor(() => app.getElement('page-view').innerHTML.includes('Workflow metadata'));
  assert.match(app.getElement('page-view').innerHTML, /Pending approval lifecycle is preserved/);
  assert.match(app.getElement('page-view').innerHTML, /approval .*pending/);
  assert.doesNotMatch(app.getElement('page-view').innerHTML, /data-action="resume-workflow"/);
  assert.match(app.getElement('page-view').innerHTML, /data-action="pause-workflow"/);
  assert.ok(requests.some((request) => request.path === '/api/v1/operator/workflows/workflow-1'));
});

test('an expired workflow approval cannot expose the resume action', async () => {
  const requests = [];
  const app = setupDashboard(apiFetch(requests, [], 'paused', 'approved', '2020-01-01T13:00:00Z'));
  await app.getElement('consent-connect').click();
  await waitFor(() => app.getElement('app-shell').hidden === false);

  app.context.location.hash = '#workflow/workflow-1';
  await app.window.dispatch('hashchange');
  await waitFor(() => app.getElement('page-view').innerHTML.includes('Workflow metadata'));
  const markup = app.getElement('page-view').innerHTML;
  assert.match(markup, /Pending approval lifecycle is preserved/);
  assert.doesNotMatch(markup, /data-action="resume-workflow"/);
});

test('approved workflow can resume through the server with confirmation and correct endpoint', async () => {
  const requests = [];
  const app = setupDashboard(apiFetch(requests, [], 'paused', 'approved'));
  await app.getElement('consent-connect').click();
  await waitFor(() => app.getElement('app-shell').hidden === false);

  app.getElement('project-selector').value = 'demo';
  await app.getElement('project-selector').dispatch('change');
  await waitFor(() => requests.some((request) => request.path === '/api/v1/operator/status?project_id=demo'));
  app.context.location.hash = '#workflow/workflow-1';
  await app.window.dispatch('hashchange');
  await waitFor(() => app.getElement('page-view').innerHTML.includes('Workflow metadata'));
  const resume = app.document.querySelectorAll('[data-action]').find((element) => element.dataset.action === 'resume-workflow');
  assert.ok(resume);
  const operation = resume.click();
  await waitFor(() => app.getElement('confirm-dialog').open);
  assert.equal(requests.some((request) => request.path.endsWith('/resume') && request.options.method === 'POST'), false);
  await app.getElement('confirm-form').dispatch('submit');
  await operation;
  assert.equal(requests.filter((request) => request.path === '/api/v1/operator/workflows/workflow-1/resume?project_id=demo' && request.options.method === 'POST').length, 1);
  assert.equal(requests.find((request) => request.path.endsWith('/resume?project_id=demo') && request.options.method === 'POST').options.body, undefined);
});

test('real-time service health indicator displays online states, latencies, and overall badge', async () => {
  const requests = [];
  const app = setupDashboard(apiFetch(requests));
  await app.getElement('consent-connect').click();
  await waitFor(() => app.getElement('app-shell').hidden === false);

  await waitFor(() => app.getElement('overall-label').textContent.includes('Tutti i servizi operativi'));
  assert.equal(app.getElement('overall-label').textContent, 'Tutti i servizi operativi');
  assert.equal(app.getElement('backend-status-text').textContent, 'Online');
  assert.match(app.getElement('backend-latency-text').textContent, /\(1\.5 ms\)/);
  assert.equal(app.getElement('system1-status-text').textContent, 'Online');
  assert.match(app.getElement('system1-latency-text').textContent, /\(12\.4 ms\)/);
  assert.ok(requests.some((r) => r.path === '/api/v1/health/services'));
});

test('service health indicator handles partial state when System 1 is offline', async () => {
  const requests = [];
  const partialFetch = async (url, options = {}) => {
    const parsed = new URL(url, 'http://127.0.0.1:8765');
    if (parsed.pathname === '/api/v1/health/services') {
      requests.push({ path: `${parsed.pathname}${parsed.search}`, options });
      return makeResponse(200, {
        overall_status: 'partial',
        backend: { name: 'Backend FastAPI', status: 'online', endpoint: 'http://127.0.0.1:8765', latency_ms: 2.1, checked_at: '2026-01-01T12:00:00Z', message: 'Backend operativo' },
        system1: { name: 'Laya System 1', status: 'offline', endpoint: 'http://127.0.0.1:8000/v1/systemone', latency_ms: null, checked_at: '2026-01-01T12:00:00Z', message: 'System 1 non raggiungibile' },
      });
    }
    return apiFetch(requests)(url, options);
  };

  const app = setupDashboard(partialFetch);
  await app.getElement('consent-connect').click();
  await waitFor(() => app.getElement('app-shell').hidden === false);

  await waitFor(() => app.getElement('overall-label').textContent.includes('Connessione parziale'));
  assert.equal(app.getElement('overall-label').textContent, 'Connessione parziale');
  assert.equal(app.getElement('backend-status-text').textContent, 'Online');
  assert.equal(app.getElement('system1-status-text').textContent, 'Offline');
});

test('service health indicator handles both services offline / error', async () => {
  const requests = [];
  const offlineFetch = async (url, options = {}) => {
    const parsed = new URL(url, 'http://127.0.0.1:8765');
    if (parsed.pathname === '/api/v1/health/services') {
      requests.push({ path: `${parsed.pathname}${parsed.search}`, options });
      return makeResponse(200, {
        overall_status: 'offline',
        backend: { name: 'Backend FastAPI', status: 'error', endpoint: 'http://127.0.0.1:8765', latency_ms: null, checked_at: '2026-01-01T12:00:00Z', message: 'Errore interno' },
        system1: { name: 'Laya System 1', status: 'offline', endpoint: 'http://127.0.0.1:8000/v1/systemone', latency_ms: null, checked_at: '2026-01-01T12:00:00Z', message: 'Non raggiungibile' },
      });
    }
    return apiFetch(requests)(url, options);
  };

  const app = setupDashboard(offlineFetch);
  await app.getElement('consent-connect').click();
  await waitFor(() => app.getElement('app-shell').hidden === false);

  await waitFor(() => app.getElement('overall-label').textContent.includes('Servizi offline'));
  assert.equal(app.getElement('overall-label').textContent, 'Servizi offline');
  assert.equal(app.getElement('backend-status-text').textContent, 'Errore');
  assert.equal(app.getElement('system1-status-text').textContent, 'Offline');
});

test('settings allows changing polling interval, manual health check and pausing polling', async () => {
  const requests = [];
  const app = setupDashboard(apiFetch(requests));
  await app.getElement('consent-connect').click();
  await waitFor(() => app.getElement('app-shell').hidden === false);

  app.context.location.hash = '#settings';
  await app.window.dispatch('hashchange');
  await waitFor(() => app.getElement('page-view').innerHTML.includes('Polling stato servizi'));

  // Test manual "Verifica ora" action
  const checkNowBtn = app.document.querySelectorAll('[data-action]').find((e) => e.dataset.action === 'check-health-now');
  assert.ok(checkNowBtn);
  const countBefore = requests.filter((r) => r.path === '/api/v1/health/services').length;
  await checkNowBtn.click();
  const countAfter = requests.filter((r) => r.path === '/api/v1/health/services').length;
  assert.ok(countAfter > countBefore);

  // Test changing health interval to 30s
  const intervalSelect = app.getElement('health-interval');
  assert.ok(intervalSelect);
  intervalSelect.value = '30';
  await intervalSelect.dispatch('change');
  assert.equal(app.localStorage.getItem('laya-health-interval'), '30');

  // Test pausing health polling (value 0)
  intervalSelect.value = '0';
  await intervalSelect.dispatch('change');
  assert.equal(app.localStorage.getItem('laya-health-interval'), '0');
});

// --- Protezione dei workflow critici: monitoraggio, allerta, arresto, ripresa ---

const CRITICAL_WORKFLOW_ID = 'workflow-critical';
const HEALTH_SERVICES_PATH = '/api/v1/health/services';

const servicesPayload = ({ backend = 'online', system1 = 'online' }) => {
  const backendOnline = backend === 'online';
  const system1Online = system1 === 'online';
  return {
    overall_status: backendOnline && system1Online ? 'all_operational' : (backendOnline || system1Online) ? 'partial' : 'offline',
    backend: {
      name: 'Backend FastAPI', status: backend, endpoint: 'http://127.0.0.1:8765',
      latency_ms: backendOnline ? 1.5 : null, checked_at: '2026-01-01T12:00:00Z',
      message: backendOnline ? 'Backend operativo e reattivo' : 'Errore interno',
    },
    system1: {
      name: 'Laya System 1', status: system1, endpoint: 'http://127.0.0.1:8000/v1/systemone',
      latency_ms: system1Online ? 12.4 : null, checked_at: '2026-01-01T12:00:00Z',
      message: system1Online ? 'Laya System 1 daemon operativo'
        : system1 === 'error' ? 'Timeout durante la verifica di System 1'
        : 'System 1 non raggiungibile (connessione rifiutata)',
    },
  };
};

const criticalWorkflowRecord = (overrides = {}) => ({
  workflow_id: CRITICAL_WORKFLOW_ID, project_id: 'demo', status: 'running', is_critical: true,
  version: 2, created_at: '2026-01-01T10:00:00Z', updated_at: '2026-01-01T11:00:00Z',
  pending_step: 'replace', completed_steps: [], error_code: null,
  steps: [{
    step_id: 'replace', tool_id: 'file.replace', depends_on: [], status: 'running',
    execution_id: 'execution-critical', execution_status: 'running',
    approval_id: 'approval-critical', approval_status: 'pending', approval_expires_at: '2030-01-01T13:00:00Z',
  }],
  ...overrides,
});

const readOnlyWorkflowRecord = () => ({
  workflow_id: 'workflow-readonly', project_id: 'demo', status: 'running', is_critical: false,
  version: 1, created_at: '2026-01-01T10:00:00Z', updated_at: '2026-01-01T11:00:00Z',
  pending_step: null, completed_steps: [], error_code: null,
  steps: [{ step_id: 'read', tool_id: 'file.read', depends_on: [], status: 'running', execution_id: 'execution-readonly', execution_status: 'running', approval_id: null, approval_status: null, approval_expires_at: null }],
});

// Scripted backend for the critical-workflow safety tests. `control` drives the System 1
// verdict, the pause outcome, and an optional gate that holds the health request open so
// overlapping checks can be observed.
function safetyFetch(requests, control = {}) {
  const base = apiFetch(requests);
  const script = {
    backend: 'online', system1: 'online', workflows: [criticalWorkflowRecord()],
    pauseStatus: 200, healthGate: null, healthUnreachable: false, hangHealth: false, ...control,
  };
  const record = (parsed, options, extra = {}) => {
    requests.push({ path: `${parsed.pathname}${parsed.search}`, options, ...extra });
  };
  const handler = async (url, options = {}) => {
    const parsed = new URL(url, 'http://127.0.0.1:8765');
    const path = parsed.pathname;
    if (script.healthUnreachable) throw new Error('connect ECONNREFUSED 127.0.0.1:8765');
    if (path === HEALTH_SERVICES_PATH) {
      record(parsed, options);
      if (script.hangHealth) {
        // Emulate a probe that never answers until the caller's budget aborts it.
        await new Promise((_, reject) => {
          const fail = () => { const error = new Error('aborted'); error.name = 'AbortError'; reject(error); };
          if (options.signal?.aborted) fail();
          else options.signal?.addEventListener('abort', fail, { once: true });
        });
      }
      if (script.healthGate) await script.healthGate;
      return makeResponse(200, servicesPayload(script));
    }
    if (path === '/api/v1/operator/critical-alert/event' && options.method === 'POST') {
      record(parsed, options, { body: options.body });
      return makeResponse(200, { status: 'recorded' });
    }
    if (path === `/api/v1/operator/workflows/${CRITICAL_WORKFLOW_ID}/pause` && options.method === 'POST') {
      record(parsed, options);
      if (script.pauseStatus !== 200) {
        return makeResponse(script.pauseStatus, { detail: { code: 'workflow_conflict', message: 'Workflow cannot be paused from its current state.' } });
      }
      return makeResponse(200, { workflow_id: CRITICAL_WORKFLOW_ID, status: 'paused', version: 3, pending_step: 'replace' });
    }
    if (path === `/api/v1/operator/workflows/${CRITICAL_WORKFLOW_ID}/resume` && options.method === 'POST') {
      record(parsed, options);
      return makeResponse(200, { workflow_id: CRITICAL_WORKFLOW_ID, status: 'running', version: 4, pending_step: null });
    }
    if (path === '/api/v1/operator/workflows' && parsed.searchParams.has('limit')) {
      record(parsed, options);
      return makeResponse(200, script.workflows);
    }
    if (path === `/api/v1/operator/workflows/${CRITICAL_WORKFLOW_ID}`) {
      record(parsed, options);
      return makeResponse(200, criticalWorkflowRecord({ status: 'paused', approval_status: 'approved' }));
    }
    return base(url, options);
  };
  handler.script = script;
  return handler;
}

const safetyEvents = (requests, eventType) => requests
  .filter((request) => request.path === '/api/v1/operator/critical-alert/event' && request.options.method === 'POST')
  .map((request) => JSON.parse(request.body))
  .filter((event) => event.event_type === eventType);

const healthChecks = (requests) => requests.filter((request) => request.path === HEALTH_SERVICES_PATH).length;
// Workflow transitions are sent with the backend-authorized project scope in the query string.
const transitionCalls = (requests, verb) => requests.filter(
  (request) => request.path.includes(`/workflows/${CRITICAL_WORKFLOW_ID}/${verb}`) && request.options.method === 'POST',
).length;
const pauseCalls = (requests) => transitionCalls(requests, 'pause');
const resumeCalls = (requests) => transitionCalls(requests, 'resume');

// Connects through the local automatic-consent flow and waits until both the service
// health verdict and the active-workflow inspection have settled.
async function connectSafetyDashboard(requests = [], control = {}) {
  const fetchHandler = safetyFetch(requests, control);
  const app = setupDashboard(fetchHandler);
  app.getElement('priority-alert-banner').hidden = true;
  await app.getElement('consent-connect').click();
  await waitFor(() => app.getElement('app-shell').hidden === false);
  await waitFor(() => requests.some((request) => request.path === '/api/v1/operator/workflows?limit=10'));
  await waitFor(() => app.getElement('overall-label').textContent !== 'Verifica in corso...');
  return { app, requests, fetchHandler };
}

const runHealthCheck = (app) => app.getElement('alert-check-system1').click();
const repeatTimer = (app, ms) => [...app.timers.values()].some((timer) => timer.timeout === ms);

test('normal operation stays silent and a low-risk workflow never raises a priority alert', async () => {
  const requests = [];
  const { app, fetchHandler } = await connectSafetyDashboard(requests, { workflows: [readOnlyWorkflowRecord()] });

  assert.equal(app.audioBeeps.length, 0, 'no alarm during normal operation');
  assert.equal(app.getElement('priority-alert-banner').hidden, true);
  assert.equal(app.getElement('system1-status-text').textContent, 'Online');

  fetchHandler.script.system1 = 'offline';
  for (let attempt = 0; attempt < 3; attempt += 1) await runHealthCheck(app);

  assert.equal(app.getElement('system1-status-text').textContent, 'Offline');
  assert.equal(app.getElement('priority-alert-banner').hidden, true, 'a non-critical workflow must not raise a priority alert');
  assert.equal(pauseCalls(requests), 0, 'a non-critical workflow is never auto-paused by the monitor');
  assert.equal(requests.filter((request) => request.path === '/api/v1/operator/critical-alert/event').length, 0);
  assert.equal(app.audioBeeps.length, 0);
});

test('a System 1 drop during a critical workflow raises the priority alert and stops the workflow', async () => {
  const requests = [];
  const { app, fetchHandler } = await connectSafetyDashboard(requests);

  // A single failed probe is a transient error: the alert threshold is 2 consecutive checks.
  fetchHandler.script.system1 = 'offline';
  await runHealthCheck(app);
  assert.equal(app.getElement('priority-alert-banner').hidden, true, 'a single failed check must not raise the alert');
  assert.equal(pauseCalls(requests), 0);

  await runHealthCheck(app);
  await waitFor(() => app.getElement('priority-alert-banner').hidden === false);

  assert.match(htmlSource, /SYSTEM 1 OFFLINE — WORKFLOW CRITICO INTERROTTO/);
  assert.match(app.getElement('alert-workflow-name').textContent, /workflow-critical/);
  assert.equal(app.getElement('alert-execution-id').textContent, 'execution-critical');
  assert.match(app.getElement('alert-workflow-phase').textContent, /replace/);
  assert.match(app.getElement('alert-reason').textContent, /System 1 offline/);
  assert.match(app.getElement('alert-stop-status').textContent, /Arresto confermato/);
  assert.equal(app.getElement('alert-resume-workflow').hidden, true, 'resume stays blocked while System 1 is down');

  assert.equal(pauseCalls(requests), 1, 'the workflow is stopped through the existing backend transition');
  const [pauseRequest] = requests.filter((request) => request.path.includes('/pause'));
  assert.match(pauseRequest.path, /project_id=demo/, 'the stop stays inside the authorized project scope');
  const [event] = safetyEvents(requests, 'system1_failure_interruption');
  assert.equal(event.status, 'paused_safely');
  assert.equal(event.workflow_id, CRITICAL_WORKFLOW_ID);
  assert.equal(event.execution_id, 'execution-critical');
  assert.equal(event.project_id, 'demo');
  assert.equal(event.details.system1_status, 'offline');
  assert.equal(event.details.consecutive_failures, 2);
  assert.equal(event.details.stop_confirmed, true);
  assert.doesNotMatch(JSON.stringify(event), /laya_sess_test_operator_token/);
});

test('operator acknowledgment silences the alarm, records an audit event and keeps the alert visible', async () => {
  const requests = [];
  const { app, fetchHandler } = await connectSafetyDashboard(requests);
  fetchHandler.script.system1 = 'offline';
  await runHealthCheck(app);
  await runHealthCheck(app);
  await waitFor(() => app.getElement('priority-alert-banner').hidden === false);

  assert.equal(app.audioBeeps.length, 1, 'the critical alarm sounds when the alert activates');
  assert.ok(repeatTimer(app, 4000), 'the alarm repeats until the operator acknowledges');

  await app.getElement('alert-ack-btn').click();
  assert.equal(app.audioBeeps.length, 1, 'acknowledgment stops the alarm');
  assert.equal(repeatTimer(app, 4000), false, 'the repeating alarm is cancelled');
  assert.equal(app.getElement('priority-alert-banner').hidden, false, 'the visual alert stays until the problem is handled');
  assert.equal(pauseCalls(requests), 1);
  assert.equal(resumeCalls(requests), 0, 'acknowledgment never resumes the workflow');

  const [event] = safetyEvents(requests, 'operator_acknowledged');
  assert.equal(event.status, 'acknowledged');
  assert.equal(event.workflow_id, CRITICAL_WORKFLOW_ID);
  assert.equal(event.details.audio_silenced, true);
});

test('a System 1 timeout is treated as a failure and an unconfirmed stop is reported honestly', async () => {
  const requests = [];
  const { app, fetchHandler } = await connectSafetyDashboard(requests, { pauseStatus: 409 });

  fetchHandler.script.system1 = 'error';
  await runHealthCheck(app);
  await runHealthCheck(app);
  await waitFor(() => app.getElement('priority-alert-banner').hidden === false);

  assert.equal(app.getElement('system1-status-text').textContent, 'Errore');
  assert.equal(app.getElement('overall-label').textContent, 'Connessione parziale');
  assert.match(app.getElement('alert-stop-status').textContent, /Interruzione non confermata/);
  assert.equal(app.getElement('alert-resume-workflow').hidden, true);
  const [event] = safetyEvents(requests, 'system1_failure_interruption');
  assert.equal(event.status, 'stop_unconfirmed');
  assert.equal(event.details.stop_confirmed, false);
});

test('an unreachable backend is reported as unconfirmed and never claims a safe stop', async () => {
  const requests = [];
  const { app, fetchHandler } = await connectSafetyDashboard(requests);
  // The whole backend disappears while a critical workflow is running.
  fetchHandler.script.healthUnreachable = true;
  await runHealthCheck(app);
  await runHealthCheck(app);
  await waitFor(() => app.getElement('priority-alert-banner').hidden === false);

  assert.equal(app.getElement('backend-status-text').textContent, 'Offline');
  assert.equal(app.getElement('system1-status-text').textContent, 'Offline');
  assert.equal(app.getElement('overall-label').textContent, 'Servizi offline');
  assert.match(app.getElement('alert-stop-status').textContent, /Interruzione non confermata/);
  assert.equal(app.getElement('alert-resume-workflow').hidden, true, 'an unverified stop never exposes resume');
  // Nothing can be persisted while the backend itself is unreachable: the alert stays the
  // only evidence, and it must not claim a confirmed stop.
  assert.equal(safetyEvents(requests, 'system1_failure_interruption').length, 0);
  assert.equal(pauseCalls(requests), 0);
});

test('a transient error does not alert on its own and a recovery resets the failure counter', async () => {
  const requests = [];
  const { app, fetchHandler } = await connectSafetyDashboard(requests);

  fetchHandler.script.system1 = 'offline';
  await runHealthCheck(app);
  assert.equal(app.getElement('priority-alert-banner').hidden, true);

  fetchHandler.script.system1 = 'online';
  await runHealthCheck(app);
  assert.equal(app.getElement('priority-alert-banner').hidden, true);

  fetchHandler.script.system1 = 'offline';
  await runHealthCheck(app);
  assert.equal(app.getElement('priority-alert-banner').hidden, true, 'a recovery resets the consecutive-failure counter');
  await runHealthCheck(app);
  await waitFor(() => app.getElement('priority-alert-banner').hidden === false);
  assert.equal(pauseCalls(requests), 1);
});

test('recovery offers a controlled resume without restarting the workflow automatically', async () => {
  const requests = [];
  const { app, fetchHandler } = await connectSafetyDashboard(requests);
  fetchHandler.script.system1 = 'offline';
  await runHealthCheck(app);
  await runHealthCheck(app);
  await waitFor(() => app.getElement('priority-alert-banner').hidden === false);

  fetchHandler.script.system1 = 'online';
  await runHealthCheck(app);

  assert.equal(app.getElement('system1-status-text').textContent, 'Online');
  assert.equal(app.getElement('alert-system1-state').textContent, 'Ripristinato (Online)');
  assert.equal(app.getElement('alert-resume-workflow').hidden, false, 'resume is offered only after a verified recovery');
  assert.equal(app.getElement('priority-alert-banner').hidden, false, 'the interruption stays visible until handled');
  assert.equal(resumeCalls(requests), 0, 'recovery never restarts the workflow on its own');
});

test('resuming an interrupted critical workflow requires an explicit operator confirmation', async () => {
  const requests = [];
  const { app, fetchHandler } = await connectSafetyDashboard(requests);
  fetchHandler.script.system1 = 'offline';
  await runHealthCheck(app);
  await runHealthCheck(app);
  await waitFor(() => app.getElement('priority-alert-banner').hidden === false);
  fetchHandler.script.system1 = 'online';
  await runHealthCheck(app);

  const declined = app.getElement('alert-resume-workflow').click();
  await waitFor(() => app.getElement('confirm-dialog').open);
  assert.match(app.getElement('confirm-eyebrow').textContent, /RIPRESA SICURA/);
  assert.match(app.getElement('confirm-description').textContent, /online/);
  await app.getElement('confirm-cancel').click();
  await declined;
  assert.equal(resumeCalls(requests), 0, 'a declined confirmation never resumes the workflow');
  assert.equal(app.getElement('priority-alert-banner').hidden, false);

  const accepted = app.getElement('alert-resume-workflow').click();
  await waitFor(() => app.getElement('confirm-dialog').open);
  await app.getElement('confirm-form').dispatch('submit');
  await accepted;

  assert.equal(resumeCalls(requests), 1);
  assert.equal(app.getElement('priority-alert-banner').hidden, true, 'the alert closes once the workflow is resumed');
  const [event] = safetyEvents(requests, 'operator_resumed_workflow');
  assert.equal(event.status, 'resumed');
  assert.equal(event.workflow_id, CRITICAL_WORKFLOW_ID);
  assert.equal(event.details.new_status, 'running');
  assert.equal(event.details.system1_latency, 12.4);
});

test('resume is refused while System 1 is still offline', async () => {
  const requests = [];
  const { app, fetchHandler } = await connectSafetyDashboard(requests);
  fetchHandler.script.system1 = 'offline';
  await runHealthCheck(app);
  await runHealthCheck(app);
  await waitFor(() => app.getElement('priority-alert-banner').hidden === false);

  await app.getElement('alert-resume-workflow').click();
  assert.equal(app.getElement('confirm-dialog').open, false, 'no confirmation is offered while System 1 is down');
  assert.equal(resumeCalls(requests), 0);
  assert.equal(app.getElement('priority-alert-banner').hidden, false);
});

test('the alert can navigate the operator to the interrupted workflow', async () => {
  const requests = [];
  const { app, fetchHandler } = await connectSafetyDashboard(requests);
  fetchHandler.script.system1 = 'offline';
  await runHealthCheck(app);
  await runHealthCheck(app);
  await waitFor(() => app.getElement('priority-alert-banner').hidden === false);

  await app.getElement('alert-view-workflow').click();
  assert.equal(app.context.location.hash, `#workflow/${CRITICAL_WORKFLOW_ID}`);
});

test('disconnecting the operator during an alert silences it and clears the banner', async () => {
  const requests = [];
  const { app, fetchHandler } = await connectSafetyDashboard(requests);
  fetchHandler.script.system1 = 'offline';
  await runHealthCheck(app);
  await runHealthCheck(app);
  await waitFor(() => app.getElement('priority-alert-banner').hidden === false);
  assert.equal(app.audioBeeps.length, 1);

  await app.getElement('disconnect-button').click();
  await waitFor(() => app.getElement('welcome-screen').hidden === false);
  assert.equal(app.getElement('priority-alert-banner').hidden, true);
  assert.equal(repeatTimer(app, 4000), false, 'no alarm survives a disconnection');
  assert.ok(requests.some((request) => request.path === '/api/v1/operator/session/disconnect' && request.options.method === 'POST'));
});

test('a critical workflow switches health polling to the fast interval without overlapping requests', async () => {
  const requests = [];
  const { app, fetchHandler } = await connectSafetyDashboard(requests);

  assert.ok(repeatTimer(app, 2000), 'a critical workflow uses the fast monitoring interval');

  // Hold the next health request open: a second check must not be sent while one is in flight.
  let release;
  fetchHandler.script.healthGate = new Promise((resolve) => { release = resolve; });
  const before = healthChecks(requests);
  const inFlight = runHealthCheck(app);
  await waitFor(() => healthChecks(requests) === before + 1);
  runHealthCheck(app);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(healthChecks(requests), before + 1, 'overlapping health requests are suppressed');
  fetchHandler.script.healthGate = null;
  release();
  await inFlight;
  assert.equal(app.getElement('system1-status-text').textContent, 'Online');
});

test('the configured ordinary polling interval applies when no critical workflow is active', async () => {
  const requests = [];
  const { app, fetchHandler } = await connectSafetyDashboard(requests, { workflows: [readOnlyWorkflowRecord()] });

  assert.equal(repeatTimer(app, 2000), false, 'no fast polling without a critical workflow');
  assert.ok(repeatTimer(app, 15000), 'the default 15s ordinary interval is kept');

  app.context.location.hash = '#settings';
  await app.window.dispatch('hashchange');
  await waitFor(() => app.getElement('page-view').innerHTML.includes('Polling stato servizi'));
  const intervalSelect = app.getElement('health-interval');
  intervalSelect.value = '30';
  await intervalSelect.dispatch('change');
  assert.equal(app.localStorage.getItem('laya-health-interval'), '30');
  assert.ok(repeatTimer(app, 30000), 'the configured interval is applied');
  assert.equal(repeatTimer(app, 15000), false);
});

test('a critical probe honours the configured timeout budget', async () => {
  const requests = [];
  const { app, fetchHandler } = await connectSafetyDashboard(requests);
  assert.equal(repeatTimer(app, 1000), false, 'ordinary polling keeps the general request budget');

  app.context.location.hash = '#settings';
  await app.window.dispatch('hashchange');
  await waitFor(() => app.getElement('page-view').innerHTML.includes('Timeout massimo'));
  const timeoutSelect = app.getElement('health-timeout');
  timeoutSelect.value = '1000';
  await timeoutSelect.dispatch('change');
  assert.equal(app.localStorage.getItem('laya-alert-timeout-ms'), '1000');

  fetchHandler.script.hangHealth = true;
  const check = runHealthCheck(app);
  await waitFor(() => repeatTimer(app, 1000));
  app.fireTimers(1000);
  await check;

  assert.equal(app.getElement('backend-status-text').textContent, 'Offline');
  assert.match(app.getElement('backend-health-pill').title, /Timeout/);
  assert.equal(repeatTimer(app, 1000), false, 'the expired budget is cleared');
});

test('the alert sound can be tested on demand and muted without hiding the visual alert', async () => {
  const requests = [];
  const { app, fetchHandler } = await connectSafetyDashboard(requests);

  app.context.location.hash = '#settings';
  await app.window.dispatch('hashchange');
  await waitFor(() => app.getElement('page-view').innerHTML.includes('Canali di avviso'));
  const soundTest = app.document.querySelectorAll('[data-action]').find((element) => element.dataset.action === 'test-alert-sound');
  await soundTest.click();
  assert.equal(app.audioBeeps.length, 1, 'the sound test emits one beep');

  const audioSelect = app.getElement('alert-audio');
  audioSelect.value = 'false';
  await audioSelect.dispatch('change');
  assert.equal(app.localStorage.getItem('laya-alert-audio'), 'false');

  fetchHandler.script.system1 = 'offline';
  await runHealthCheck(app);
  await runHealthCheck(app);
  await waitFor(() => app.getElement('priority-alert-banner').hidden === false);
  assert.equal(app.getElement('priority-alert-banner').hidden, false, 'muting audio keeps the visual alert');
  assert.equal(app.audioBeeps.length, 1, 'no further beep is emitted once audio is disabled');
});
