/* Laya Pro — UI client */

const $ = (id) => document.getElementById(id);

const el = {
  app: $('app'),
  sidebar: $('sidebar'),
  scrim: $('scrim'),
  sessionList: $('session-list'),
  engineChips: $('engine-chips'),
  messages: $('messages'),
  input: $('input'),
  send: $('send'),
  notice: $('notice'),
  toast: $('toast'),
  topbarTitle: $('topbar-title'),
  topbarSub: $('topbar-sub'),
  hintTier: $('hint-tier'),
  hintFlow: $('hint-flow'),
  autoMemory: $('auto-memory'),
  themeLabel: $('theme-label'),
};

const TIERS = {
  LOW: {
    flow: 'Nemotron risponde direttamente',
    stages: [{ stage: 'nemotron.generate', engine: 'nemotron' }],
  },
  MEDIUM: {
    flow: 'laya-coreml pianifica → Nemotron scrive',
    stages: [
      { stage: 'laya.reason', engine: 'laya' },
      { stage: 'nemotron.generate', engine: 'nemotron' },
    ],
  },
  HARD: {
    flow: 'laya-coreml pianifica → Nemotron scrive → laya-coreml critica',
    stages: [
      { stage: 'laya.reason', engine: 'laya' },
      { stage: 'nemotron.generate', engine: 'nemotron' },
      { stage: 'laya.critique', engine: 'laya' },
    ],
  },
};

const state = {
  sessionId: null,
  tier: 'MEDIUM',
  busy: false,
  engineLabels: {},
};

// ---------------------------------------------------------------- api

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.detail || `Errore ${response.status}`);
    error.status = response.status;
    error.engine = payload.engine;
    throw error;
  }
  return payload;
}

// ---------------------------------------------------------------- rendering

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** Minimal Markdown: code fences, inline code, bold, italic, lists, headings. */
function renderMarkdown(text) {
  const fences = [];
  let work = String(text).replace(/```(\w+)?\n?([\s\S]*?)```/g, (_, lang, code) => {
    fences.push(`<pre><code data-lang="${escapeHtml(lang || '')}">${escapeHtml(code)}</code></pre>`);
    return `\u0000FENCE${fences.length - 1}\u0000`;
  });

  work = escapeHtml(work);
  work = work.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  work = work.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  work = work.replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>');

  const lines = work.split('\n');
  const out = [];
  let listType = null;

  const closeList = () => { if (listType) { out.push(`</${listType}>`); listType = null; } };

  for (const line of lines) {
    const trimmed = line.trim();
    const fence = trimmed.match(/^\u0000FENCE(\d+)\u0000$/);
    if (fence) { closeList(); out.push(fences[Number(fence[1])]); continue; }

    const bullet = trimmed.match(/^[-*]\s+(.*)$/);
    const numbered = trimmed.match(/^\d+\.\s+(.*)$/);

    if (bullet || numbered) {
      const wanted = bullet ? 'ul' : 'ol';
      if (listType !== wanted) { closeList(); out.push(`<${wanted}>`); listType = wanted; }
      out.push(`<li>${bullet ? bullet[1] : numbered[1]}</li>`);
      continue;
    }
    closeList();

    const heading = trimmed.match(/^#{1,6}\s+(.*)$/);
    if (heading) { out.push(`<h3>${heading[1]}</h3>`); continue; }
    if (!trimmed) { continue; }
    out.push(`<p>${trimmed}</p>`);
  }
  closeList();

  return out.join('');
}

function emptyView() {
  const wrap = document.createElement('div');
  wrap.className = 'empty';
  wrap.innerHTML = `
    <div class="empty-mark">⚡</div>
    <h2>Come posso aiutarti?</h2>
    <p>Una domanda va al modello veloce.<br>
       Un problema strutturato viene prima ragionato da <b>laya-coreml</b>,
       poi tradotto in parole da <b>Nemotron 3.5 Lightning</b>.</p>
    <div class="suggestions">
      <button class="suggestion" data-tier="LOW">
        <span class="tag fast">LOW</span>
        <span>Spiegami la differenza tra System 1 e System 2</span>
      </button>
      <button class="suggestion" data-tier="MEDIUM">
        <span class="tag">MEDIUM</span>
        <span>Progetta un refactor a passi di un modulo di autenticazione</span>
      </button>
      <button class="suggestion" data-tier="HARD">
        <span class="tag">HARD</span>
        <span>Rivedi questo piano di deploy confrontando i vincoli di sicurezza</span>
      </button>
    </div>`;
  wrap.querySelectorAll('.suggestion').forEach((button) => {
    button.addEventListener('click', () => {
      const text = button.querySelector('span:last-child').textContent;
      setTier(button.dataset.tier);
      el.input.value = text;
      autoGrow();
      el.input.focus();
    });
  });
  return wrap;
}

function messageNode(role, { text = '', tier = null, response = null } = {}) {
  const node = document.createElement('div');
  node.className = `msg ${role}`;

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = role === 'user' ? 'TU' : '⚡';

  const body = document.createElement('div');
  body.className = 'msg-body';

  const head = document.createElement('div');
  head.className = 'msg-head';
  head.innerHTML = `
    <span class="msg-author">${role === 'user' ? 'Tu' : 'Laya'}</span>
    ${tier ? `<span class="msg-tier">${escapeHtml(tier)}</span>` : ''}`;
  body.appendChild(head);

  const content = document.createElement('div');
  content.className = 'msg-content';
  content.innerHTML = renderMarkdown(text);
  body.appendChild(content);

  if (response) body.appendChild(pipelineNode(response));
  else body.appendChild(actionsNode(text));

  node.appendChild(avatar);
  node.appendChild(body);
  return node;
}

function actionsNode(text) {
  const wrap = document.createElement('div');
  wrap.className = 'msg-actions';
  const button = document.createElement('button');
  button.className = 'act-btn';
  button.innerHTML = `<svg viewBox="0 0 24 24" class="icon"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>Copia`;
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast('Copiato');
    } catch {
      toast('Impossibile copiare', true);
    }
  });
  wrap.appendChild(button);
  return wrap;
}

function engineLabel(stage) {
  const raw = stage.engine || '';
  return state.engineLabels[raw] || raw;
}

function pipelineNode(response) {
  const wrap = document.createElement('div');
  wrap.className = 'pipeline';

  const trace = response.trace || [];
  const deepMs = trace.filter((s) => s.engine === state.engineLabels.laya).reduce((a, s) => a + s.latency_ms, 0);
  const totalMs = trace.reduce((a, s) => a + s.latency_ms, 0);

  const metrics = [
    `${trace.length} ${trace.length === 1 ? 'chiamata' : 'chiamate'}`,
    `${totalMs} ms`,
  ];
  if (response.refinements) {
    const n = response.refinements;
    metrics.push(`${n} ${n === 1 ? 'revisione' : 'revisioni'}`);
  }

  const head = document.createElement('button');
  head.className = 'pipeline-head';
  head.innerHTML = `
    <svg viewBox="0 0 24 24" class="icon"><path d="m9 18 6-6-6-6"/></svg>
    <span>Pipeline</span>
    <span class="pipeline-metrics">
      <span>${escapeHtml(metrics.join(' · '))}</span>
      ${deepMs ? `<span>${deepMs} ms ragionamento</span>` : ''}
    </span>`;
  head.addEventListener('click', () => {
    wrap.classList.toggle('is-open');
    if (wrap.classList.contains('is-open')) scrollToEnd();
  });
  wrap.appendChild(head);

  const body = document.createElement('div');
  body.className = 'pipeline-body';

  for (const step of trace) {
    const isDeep = step.engine === state.engineLabels.laya;
    const row = document.createElement('div');
    row.className = `stage ${isDeep ? 'deep' : 'fast'}`;
    row.innerHTML = `
      <span class="dot"></span>
      <span class="stage-name">${escapeHtml(step.stage)}</span>
      <span class="stage-engine">${escapeHtml(engineLabel(step))}</span>
      ${step.detail ? `<span class="stage-detail">· ${escapeHtml(step.detail)}</span>` : ''}
      <span class="stage-ms">${step.latency_ms} ms</span>`;
    body.appendChild(row);
  }

  const status = document.createElement('div');
  status.className = 'status-row';
  const degraded = response.status === 'degraded';
  const hits = (response.metadata && response.metadata.memory_hits) || 0;
  status.innerHTML = `
    <span class="badge ${degraded ? 'degraded' : 'success'}">${escapeHtml(response.status)}</span>
    ${response.confidence != null ? `<span>confidenza ${Math.round(response.confidence * 100)}%</span>` : ''}
    ${hits ? `<span>· ${hits} ${hits === 1 ? 'ricordo usato' : 'ricordi usati'}</span>` : ''}`;
  body.appendChild(status);

  wrap.appendChild(body);
  return wrap;
}

function thinkingNode() {
  const node = document.createElement('div');
  node.className = 'msg assistant';
  node.innerHTML = `
    <div class="avatar">⚡</div>
    <div class="msg-body">
      <div class="msg-head"><span class="msg-author">Laya</span></div>
      <div class="thinking"><span class="spinner"></span><span id="thinking-label">...</span></div>
    </div>`;
  return node;
}

function renderMessages(messages, traces = new Map()) {
  el.messages.innerHTML = '';
  const inner = document.createElement('div');
  inner.className = 'messages-inner';

  if (!messages.length) {
    inner.appendChild(emptyView());
  } else {
    for (const message of messages) {
      inner.appendChild(
        messageNode(message.role, {
          text: message.content,
          tier: message.tier,
          response: message.role === 'assistant' ? traces.get(message.content) : null,
        })
      );
    }
  }
  el.messages.appendChild(inner);
  scrollToEnd();
}

function appendMessage(node) {
  let inner = el.messages.querySelector('.messages-inner');
  const empty = inner.querySelector('.empty');
  if (empty) { inner.innerHTML = ''; }
  inner = el.messages.querySelector('.messages-inner') || inner;
  inner.appendChild(node);
  scrollToEnd();
  return node;
}

function scrollToEnd() {
  requestAnimationFrame(() => { el.messages.scrollTop = el.messages.scrollHeight; });
}

// ---------------------------------------------------------------- sessions

function sessionItem(session, label) {
  const button = document.createElement('button');
  button.className = 'session-item' + (session.session_id === state.sessionId ? ' is-active' : '');
  button.innerHTML = `
    <svg viewBox="0 0 24 24" class="icon"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z"/></svg>
    <span class="label">${escapeHtml(label || 'Nuova chat')}</span>`;
  button.addEventListener('click', () => openSession(session.session_id));
  return button;
}

async function loadSessions() {
  try {
    const data = await api('/sessions?limit=40');
    el.sessionList.innerHTML = '';

    if (!data.sessions.length) {
      const empty = document.createElement('div');
      empty.className = 'session-group';
      empty.textContent = 'Nessuna chat';
      el.sessionList.appendChild(empty);
      return;
    }

    const group = document.createElement('div');
    group.className = 'session-group';
    group.textContent = 'Recenti';
    el.sessionList.appendChild(group);

    for (const session of data.sessions) {
      const messages = await api(
        `/sessions/${session.session_id}/messages?limit=1&direction=asc`
      );
      const first = messages.messages[0];
      const label = first ? first.content.slice(0, 42) : 'Nuova chat';
      el.sessionList.appendChild(sessionItem(session, label));
    }
  } catch (error) {
    console.warn('sessioni non disponibili', error);
  }
}

async function openSession(sessionId) {
  state.sessionId = sessionId;
  el.app.classList.remove('nav-open');
  try {
    const data = await api(`/sessions/${sessionId}/messages?limit=100`);
    const traces = await loadTraces(sessionId);
    renderMessages(data.messages, traces);
    const firstUser = data.messages.find((m) => m.role === 'user');
    el.topbarTitle.textContent = firstUser
      ? firstUser.content.slice(0, 34)
      : 'Laya Pro';
  } catch (error) {
    renderMessages([]);
    toast(error.message, true);
  }
  loadSessions();
}

/** Traces are keyed by the answer they produced, so a reload still shows the pipeline. */
async function loadTraces(sessionId) {
  try {
    const data = await api(`/sessions/${sessionId}/traces?limit=200`);
    const byText = new Map();
    for (const row of data.traces) {
      if (row.payload && row.payload.text) byText.set(row.payload.text, row.payload);
    }
    return byText;
  } catch {
    return new Map();
  }
}

function newChat() {
  state.sessionId = null;
  renderMessages([]);
  el.topbarTitle.textContent = 'Laya Pro';
  el.app.classList.remove('nav-open');
  loadSessions();
  el.input.focus();
}

// ---------------------------------------------------------------- status

function engineChip(name, label, cls, info) {
  const chip = document.createElement('div');
  const broken = cls && !info.configured;
  chip.className = `engine-chip ${cls}` + (broken ? ' is-down' : '');
  const stat = info.average_latency_ms != null
    ? `${info.average_latency_ms} ms`
    : (broken ? 'non config.' : 'pronto');
  chip.innerHTML = `
    <span class="dot"></span>
    <span class="name">${escapeHtml(label)}</span>
    <span class="stat">${escapeHtml(stat)}</span>`;
  return chip;
}

async function refreshStatus() {
  try {
    const data = await api('/status');
    state.engineLabels = {
      laya: data.engines['laya-coreml'].model,
      nemotron: data.engines.nemotron.model,
    };

    el.engineChips.innerHTML = '';
    el.engineChips.appendChild(
      engineChip('laya', 'laya-coreml', 'deep', data.engines['laya-coreml'])
    );
    el.engineChips.appendChild(
      engineChip('nemotron', 'Nemotron 3.5', 'fast', data.engines.nemotron)
    );

    const layaDown = !data.engines['laya-coreml'].configured;
    const nemotronDown = !data.engines.nemotron.configured;

    if (nemotronDown || layaDown) {
      const parts = [];
      if (nemotronDown) parts.push('Manca <code>NEMOTRON_API_KEY</code>: nessuna modalità può rispondere.');
      if (layaDown) parts.push('laya-coreml non raggiungibile: MEDIUM e HARD falliranno con 503.');
      showNotice(parts.join(' '), nemotronDown);
    } else {
      hideNotice();
    }
  } catch (error) {
    console.warn('status non disponibile', error);
  }
}

function showNotice(html, isError) {
  el.notice.innerHTML = html;
  el.notice.className = 'notice' + (isError ? ' is-error' : '');
  el.notice.hidden = false;
}

function hideNotice() { el.notice.hidden = true; }

// ---------------------------------------------------------------- send

function setTier(tier) {
  state.tier = tier;
  document.querySelectorAll('.tier-btn').forEach((button) => {
    button.classList.toggle('is-active', button.dataset.tier === tier);
  });
  el.hintTier.textContent = tier;
  el.hintFlow.textContent = TIERS[tier].flow;
  el.topbarSub.textContent = tier === 'LOW'
    ? 'Solo Nemotron'
    : tier === 'MEDIUM' ? 'Ragionamento + risposta' : 'Ragionamento + revisione';
}

async function send() {
  const text = el.input.value.trim();
  if (!text || state.busy) return;

  const tier = state.tier;
  const isNew = !state.sessionId;
  const pendingSession = state.sessionId;

  el.input.value = '';
  autoGrow();
  setBusy(true);
  appendMessage(messageNode('user', { text, tier }));

  const pending = appendMessage(thinkingNode());
  const label = pending.querySelector('#thinking-label');

  const plan = TIERS[tier].stages;
  let step = 0;
  const ticker = setInterval(() => {
    const current = plan[Math.min(step, plan.length - 1)];
    label.textContent = current.engine === 'laya'
      ? 'laya-coreml sta ragionando…'
      : 'Nemotron sta scrivendo…';
    step += 1;
  }, 1400);

  try {
    const response = await api('/chat', {
      method: 'POST',
      body: JSON.stringify({
        message: text,
        tier,
        auto_memory: el.autoMemory.checked,
        ...(state.sessionId ? { session_id: state.sessionId } : {}),
      }),
    });

    clearInterval(ticker);
    state.sessionId = response.session_id;

    const replacement = messageNode('assistant', {
      text: response.text, tier, response,
    });
    pending.replaceWith(replacement);
    el.topbarTitle.textContent = text.slice(0, 34);

    if (response.status === 'degraded') {
      toast('Risposta incompleta: budget di revisione esaurito', true);
    }

    if (isNew || pendingSession !== response.session_id) loadSessions();
  } catch (error) {
    clearInterval(ticker);
    const failed = document.createElement('div');
    failed.className = 'msg assistant';

    const detail = error.engine
      ? `<br><span style="opacity:.75">Motore: ${escapeHtml(error.engine)}</span>`
      : '';
    const hint = error.status === 503
      ? '<br><span style="opacity:.75">Usa LOW, oppure avvia laya-coreml. Il ragionamento non viene aggirato di nascosto.</span>'
      : '';

    failed.innerHTML = `
      <div class="avatar">⚠</div>
      <div class="msg-body">
        <div class="msg-head">
          <span class="msg-author">Laya</span>
          <span class="msg-tier">${tier}</span>
        </div>
        <div class="msg-content">
          <p>${escapeHtml(error.message)}${detail}${hint}</p>
        </div>
      </div>`;
    pending.replaceWith(failed);
  } finally {
    setBusy(false);
    el.input.focus();
  }
}

function setBusy(busy) {
  state.busy = busy;
  el.send.disabled = busy;
  document.querySelectorAll('.tier-btn').forEach((b) => { b.disabled = busy; });
  el.input.setAttribute('aria-busy', String(busy));
}

// ---------------------------------------------------------------- memory drawer

const memory = {
  drawer: $('memory-drawer'),
  scrim: $('drawer-scrim'),
  form: $('memory-form'),
  key: $('memory-key'),
  value: $('memory-value'),
  search: $('memory-search'),
  list: $('memory-list'),
  count: $('memory-count'),
  entries: [],
};

async function loadMemory() {
  try {
    const data = await api('/memory');
    memory.entries = data.entries;
    memory.count.textContent = data.count ? String(data.count) : '';
    renderMemory(memory.search.value.trim());
  } catch (error) {
    console.warn('memoria non disponibile', error);
  }
}

function renderMemory(filter) {
  memory.list.innerHTML = '';

  const needle = (filter || '').toLowerCase();
  const matches = needle
    ? memory.entries.filter((entry) =>
        entry.key.toLowerCase().includes(needle) ||
        entry.value.toLowerCase().includes(needle)
      )
    : memory.entries;

  if (!matches.length) {
    const empty = document.createElement('div');
    empty.className = 'memory-empty';
    empty.innerHTML = needle
      ? `Nessun ricordo corrisponde a "<b>${escapeHtml(needle)}</b>".`
      : `<b>Nessun ricordo, per ora</b>Laya estrae i fatti dalle conversazioni,<br>oppure aggiungili tu qui sopra.`;
    memory.list.appendChild(empty);
    return;
  }

  for (const entry of matches) memory.list.appendChild(memoryItem(entry));
}

function memoryItem(entry) {
  const row = document.createElement('div');
  row.className = 'memory-item';

  const body = document.createElement('div');
  body.className = 'memory-body';

  const when = entry.updated_at ? entry.updated_at.slice(0, 16) : '';

  body.innerHTML = `
    <div class="memory-key">${escapeHtml(entry.key)}</div>
    <div class="memory-value">${escapeHtml(entry.value)}</div>
    <div class="memory-meta">
      <span class="pin ${entry.pinned ? '' : 'auto'}">${
        entry.pinned ? 'PINNATO' : 'AUTO'
      }</span>
      ${when ? `<span class="memory-when">${escapeHtml(when)}</span>` : ''}
    </div>`;

  const remove = document.createElement('button');
  remove.className = 'memory-del';
  remove.title = 'Dimentica';
  remove.innerHTML = `<svg viewBox="0 0 24 24" class="icon"><path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13h10l1-13"/></svg>`;
  remove.addEventListener('click', async () => {
    try {
      await api(`/memory?key=${encodeURIComponent(entry.key)}`, { method: 'DELETE' });
      memory.entries = memory.entries.filter((item) => item.key !== entry.key);
      memory.count.textContent = memory.entries.length ? String(memory.entries.length) : '';
      renderMemory(memory.search.value.trim());
      toast('Ricordo dimenticato');
    } catch (error) {
      toast(error.message, true);
    }
  });

  row.appendChild(body);
  row.appendChild(remove);
  return row;
}

async function addMemory(event) {
  event.preventDefault();
  const key = memory.key.value.trim();
  const value = memory.value.value.trim();
  if (!key || !value) return;

  try {
    await api('/memory', {
      method: 'POST',
      body: JSON.stringify({ key, value }),
    });
    memory.key.value = '';
    memory.value.value = '';
    memory.key.focus();
    await loadMemory();
    toast('Ricordo salvato');
  } catch (error) {
    toast(error.message, true);
  }
}

function toggleMemory(open) {
  memory.drawer.hidden = !open;
  memory.scrim.hidden = !open;
  if (open) {
    loadMemory();
    memory.search.focus();
  } else {
    memory.key.value = '';
    memory.value.value = '';
  }
}

// ---------------------------------------------------------------- misc

function toast(message, isError) {
  el.toast.textContent = message;
  el.toast.className = 'toast' + (isError ? ' is-error' : '');
  el.toast.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.toast.hidden = true; }, 3200);
}

function autoGrow() {
  el.input.style.height = 'auto';
  el.input.style.height = `${Math.min(el.input.scrollHeight, 220)}px`;
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem('laya-theme', theme);
  el.themeLabel.textContent = { auto: 'Tema: auto', light: 'Tema: chiaro', dark: 'Tema: scuro' }[theme];
}

// ---------------------------------------------------------------- boot

function bind() {
  el.send.addEventListener('click', send);

  el.input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });
  el.input.addEventListener('input', autoGrow);

  document.querySelectorAll('.tier-btn').forEach((button) => {
    button.addEventListener('click', () => setTier(button.dataset.tier));
  });

  $('new-chat').addEventListener('click', newChat);
  $('open-sidebar').addEventListener('click', () => el.app.classList.add('nav-open'));
  $('close-sidebar').addEventListener('click', () => el.app.classList.remove('nav-open'));
  el.scrim.addEventListener('click', () => el.app.classList.remove('nav-open'));

  $('open-memory').addEventListener('click', () => toggleMemory(true));
  $('close-memory').addEventListener('click', () => toggleMemory(false));
  memory.scrim.addEventListener('click', () => toggleMemory(false));
  memory.form.addEventListener('submit', addMemory);
  memory.search.addEventListener('input', () => renderMemory(memory.search.value.trim()));

  $('theme-toggle').addEventListener('click', () => {
    const order = ['auto', 'light', 'dark'];
    const current = document.documentElement.dataset.theme || 'auto';
    applyTheme(order[(order.indexOf(current) + 1) % order.length]);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    el.app.classList.remove('nav-open');
    if (!memory.drawer.hidden) toggleMemory(false);
  });
}

function boot() {
  bind();
  applyTheme(localStorage.getItem('laya-theme') || 'auto');
  setTier('MEDIUM');
  renderMessages([]);
  refreshStatus();
  loadSessions();
  loadMemory();
  setInterval(refreshStatus, 15000);
  el.input.focus();
}

boot();