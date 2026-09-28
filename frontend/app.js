(() => {
  'use strict';

  const API_ROOT = '/api/v1';
  const PAGE_SIZE = 20;
  const state = {
    token: '', projects: [], projectId: '', busy: new Set(), page: 'overview', approvalFilter: 'all', workflowFilter: 'all',
    executionQuery: '', executionStatus: '', executionSince: '', executionUntil: '', executionOffset: 0,
    refreshTimer: null, lastData: {}, listeners: new AbortController(), consented: false, connecting: false,
    healthTimer: null, healthCheckInProgress: false, servicesHealth: {
      backend: { status: 'checking', message: 'In attesa di verifica...', latency_ms: null, checked_at: null },
      system1: { status: 'checking', message: 'In attesa di verifica...', latency_ms: null, checked_at: null },
      overall: 'checking',
    },
    // Monitoraggio dei workflow critici e allerta
    criticalWorkflow: null, // Workflow critico attivo se presente
    consecutiveSystem1Failures: 0,
    alertActive: false,
    alertAcknowledged: false,
    alertAudioInterval: null,
    audioMuted: false,
    audioContext: null,
  };
  const $ = (selector) => document.querySelector(selector);
  const elements = {
    view: $('#page-view'),
    welcomeScreen: $('#welcome-screen'),
    welcomeCard: document.querySelector('.welcome-card'),
    welcomeExitCard: $('#welcome-exit-card'),
    welcomeStatusBox: $('#welcome-status-box'),
    welcomeStatusText: $('#welcome-status-text'),
    welcomeErrorDetail: $('#welcome-error-detail'),
    welcomeSpinner: $('#welcome-spinner'),
    consentConnect: $('#consent-connect'),
    consentExit: $('#consent-exit'),
    consentRetryExit: $('#consent-retry-exit'),
    appShell: $('#app-shell'),
    disconnectButton: $('#disconnect-button'),
    confirmDialog: $('#confirm-dialog'),
    confirmForm: $('#confirm-form'),
    toast: $('#toast-region'),
    sidebar: $('#sidebar'),
    connection: $('#connection-status'),
    connectionLabel: $('#connection-label'),
    projectContext: $('#project-context'),
    projectSelector: $('#project-selector'),
    // Elementi indicatore di salute servizi in tempo reale
    servicesHealthBar: $('#services-health-bar'),
    overallBadge: $('#overall-service-badge'),
    overallDot: $('#overall-dot'),
    overallLabel: $('#overall-label'),
    backendDot: $('#backend-dot'),
    backendStatusText: $('#backend-status-text'),
    backendLatencyText: $('#backend-latency-text'),
    system1Dot: $('#system1-dot'),
    system1StatusText: $('#system1-status-text'),
    system1LatencyText: $('#system1-latency-text'),
    // Elementi allerta visiva prioritaria
    priorityAlertBanner: $('#priority-alert-banner'),
    alertWorkflowName: $('#alert-workflow-name'),
    alertExecutionId: $('#alert-execution-id'),
    alertDetectedTime: $('#alert-detected-time'),
    alertSystem1State: $('#alert-system1-state'),
    alertWorkflowPhase: $('#alert-workflow-phase'),
    alertReason: $('#alert-reason'),
    alertStopStatus: $('#alert-stop-status'),
    alertViewWorkflow: $('#alert-view-workflow'),
    alertAckBtn: $('#alert-ack-btn'),
    alertCheckSystem1: $('#alert-check-system1'),
    alertResumeWorkflow: $('#alert-resume-workflow'),
  };

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const shortId = (value, n = 8) => { const text = String(value || '—'); return text.length > n * 2 + 3 ? `${text.slice(0, n)}…${text.slice(-n)}` : text; };
  const fmtTime = (value, seconds = false) => { if (!value) return 'Non registrato'; const date = new Date(value); return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: seconds ? 'medium' : 'short' }).format(date) : 'Sconosciuto'; };
  const fmtTimeOnly = (value) => { if (!value) return '--:--:--'; const date = new Date(value); return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(date) : '--:--:--'; };
  const fmtRelative = (value) => { if (!value) return 'Nessun controllo riuscito'; const delta = Date.now() - new Date(value).getTime(); if (!Number.isFinite(delta)) return 'Sconosciuto'; if (delta < 60_000) return 'Proprio ora'; if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m fa`; return fmtTime(value); };
  const fmtDuration = (ms) => ms == null ? 'In corso' : ms < 1000 ? `${Math.max(0, Math.round(ms))} ms` : ms < 60_000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.floor(ms / 60_000)}m ${Math.floor((ms % 60_000) / 1000)}s`;
  const badge = (status, label = status) => `<span class="badge badge-${esc(String(status || 'unknown').toLowerCase())}">${esc((label || 'Unknown').replaceAll('_', ' '))}</span>`;
  const busy = (key) => state.busy.has(key);
  const setBusy = (key, value) => {
    if (value) state.busy.add(key); else state.busy.delete(key);
    document.querySelectorAll('[data-busy-key]').forEach((button) => { if (button.dataset.busyKey === key) button.disabled = value; });
  };
  const notify = (message, kind = 'info') => {
    if (!elements.toast) return;
    const toast = document.createElement('div'); toast.className = `toast ${kind}`; toast.textContent = message;
    elements.toast.append(toast); setTimeout(() => toast.remove(), 6000);
  };
  const setConnection = (connected, label) => {
    if (elements.connection) elements.connection.className = `connection-status ${connected ? 'connected' : 'disconnected'}`;
    if (elements.connectionLabel) elements.connectionLabel.textContent = label;
  };
  function syncProjectSelector() {
    if (!elements.projectContext || !elements.projectSelector) return;
    if (!state.token) { elements.projectContext.hidden = true; return; }
    elements.projectContext.hidden = false;
    const options = state.projects.length
      ? `<option value="">All authorized projects</option>${state.projects.map((id) => `<option value="${esc(id)}">${esc(id)}</option>`).join('')}`
      : '<option value="">No configured projects</option>';
    elements.projectSelector.innerHTML = options;
    elements.projectSelector.disabled = state.projects.length === 0;
    elements.projectSelector.value = state.projectId;
  }
  function filteredOperatorPath(path) {
    if (!state.projectId || !path.startsWith('/operator/') || path.startsWith('/operator/projects') || path.startsWith('/operator/session')) return path;
    const [pathname, search = ''] = path.split('?', 2);
    const params = new URLSearchParams(search);
    params.set('project_id', state.projectId);
    return `${pathname}?${params.toString()}`;
  }

  // --- Sistema di Allerta Sonora (Web Audio API nativa senza dipendenze) ---
  function initAudioContext() {
    if (state.audioContext) return state.audioContext;
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) {
        state.audioContext = new AudioCtx();
      }
    } catch {
      // Audio disabilitato o non supportato nel runtime di test
    }
    return state.audioContext;
  }

  function playAlertBeep() {
    let audioSetting = true;
    try {
      const saved = localStorage.getItem('laya-alert-audio');
      if (saved !== null) audioSetting = saved === 'true';
    } catch {}
    if (!audioSetting || state.audioMuted) return;

    try {
      const ctx = initAudioContext();
      if (!ctx) return;
      if (ctx.state === 'suspended') {
        ctx.resume().catch(() => {});
      }
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(880, ctx.currentTime); // La5
      osc.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + 0.35); // Caduta a La4
      gain.gain.setValueAtTime(0.18, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.36);
    } catch {
      // Non bloccare in assenza di hardware audio
    }
  }

  function startAlertSoundLoop() {
    stopAlertSoundLoop();
    let repeatSec = 4;
    try {
      const saved = localStorage.getItem('laya-alert-repeat-sec');
      if (saved) repeatSec = Math.max(2, Math.min(30, Number(saved)));
    } catch {}
    playAlertBeep();
    state.alertAudioInterval = setInterval(() => {
      if (state.alertActive && !state.alertAcknowledged) {
        playAlertBeep();
      } else {
        stopAlertSoundLoop();
      }
    }, repeatSec * 1000);
  }

  function stopAlertSoundLoop() {
    if (state.alertAudioInterval) {
      clearInterval(state.alertAudioInterval);
      state.alertAudioInterval = null;
    }
  }

  class ApiError extends Error {
    constructor(status, code, message, body) {
      super(message);
      this.status = status;
      this.code = code;
      this.body = body;
    }
  }

  function formatErrorMessage(error, defaultMsg = 'Si è verificato un errore imprevisto.') {
    if (error instanceof ApiError) {
      if (error.status === 0 || error.code === 'network_error') {
        return 'Backend non raggiungibile. Verifica che il server locale sia avviato su 127.0.0.1:8765.';
      }
      if (error.code === 'timeout') {
        return 'Richiesta scaduta per timeout. Il server locale non risponde.';
      }
      if (error.status === 401 || error.code === 'operator_authentication_required') {
        return 'Sessione non autorizzata. Autorizzazione locale rifiutata o scaduta.';
      }
      if (error.status === 403 || error.code === 'project_not_authorized' || error.code === 'forbidden_origin') {
        return `Accesso vietato (${error.code}): ${error.message}`;
      }
      if (error.status === 404 || error.code === 'not_found') {
        return `Risorsa non trovata (HTTP 404): ${error.message}`;
      }
      if (error.status === 500) {
        return `Errore interno del server (HTTP 500): ${error.message}`;
      }
      if (error.status === 503) {
        return `Servizio non disponibile (HTTP 503): ${error.message}`;
      }
      return `${error.code ? `${error.code}: ` : ''}${error.message}`;
    }
    return defaultMsg;
  }

  const api = {
    async request(path, options = {}) {
      path = filteredOperatorPath(path);
      // timeoutMs is a local budget for this call only; it never travels to the backend.
      const { timeoutMs = 12_000, ...rest } = options;
      const requestOptions = { ...rest };
      const operatorToken = requestOptions.operatorToken;
      delete requestOptions.operatorToken;
      const headers = new Headers(requestOptions.headers || {});
      headers.set('Accept', 'application/json');
      if (state.token) headers.set('X-Laya-Approval-Token', state.token);
      if (operatorToken) headers.set('X-Laya-Approval-Token', operatorToken);
      if (requestOptions.body !== undefined) headers.set('Content-Type', 'application/json');
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const url = path === '/health' ? path : `${API_ROOT}${path}`;
        const response = await fetch(url, {
          ...requestOptions,
          headers,
          credentials: 'same-origin',
          cache: 'no-store',
          redirect: 'error',
          signal: controller.signal,
        });
        const type = response.headers.get('content-type') || '';
        const data = type.includes('application/json') ? await response.json().catch(() => null) : null;
        if (!response.ok) {
          const detail = data?.detail;
          const code = typeof detail === 'object' ? detail.code : 'http_error';
          let message = typeof detail === 'object' ? detail.message : typeof detail === 'string' ? detail : `Backend returned HTTP ${response.status}`;
          if (response.status === 404 && code === 'http_error') {
            message = `L'endpoint richiesto non esiste (${path}).`;
          } else if (response.status === 500 && code === 'http_error') {
            message = 'Il backend ha riscontrato un errore interno non gestito.';
          }
          throw new ApiError(response.status, code, message, data);
        }
        return data;
      } catch (error) {
        if (error instanceof ApiError) {
          if (error.status === 401 && state.consented) {
            disconnectOperatorSession('Sessione scaduta o non valida');
          }
          throw error;
        }
        throw new ApiError(
          0,
          error.name === 'AbortError' ? 'timeout' : 'network_error',
          error.name === 'AbortError'
            ? 'Request timed out. Check that the local backend is responding.'
            : 'Could not reach the local backend. Verify that it is running on this origin.',
        );
      } finally {
        clearTimeout(timeout);
      }
    },
    get(path, operatorToken, options = {}) { return api.request(path, { ...options, operatorToken }); },
    post(path, body) { return api.request(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }); },
  };

  const errorText = (error) => formatErrorMessage(error);
  const loading = () => '<div class="loading-state" role="status"><span class="spinner" aria-hidden="true"></span><p class="muted" style="font-size:10px">Loading verified backend data…</p></div>';
  const empty = (title, text, glyph = '⌁') => `<div class="empty-state"><span class="empty-icon" aria-hidden="true">${glyph}</span><strong>${esc(title)}</strong><p>${esc(text)}</p></div>`;
  const errorPanel = (error) => `<div class="error-state"><span class="empty-icon" aria-hidden="true">!</span><strong>Could not load this view</strong><p>${esc(errorText(error))}</p><button class="button button-quiet button-small" data-action="refresh">Retry</button></div>`;
  const heading = (label, title, description, actions = '') => `<div class="page-heading"><div><div class="eyebrow">${esc(label)}</div><h1>${esc(title)}</h1><p class="heading-copy">${esc(description)}</p></div><div class="heading-actions">${actions}</div></div>`;
  const panel = (title, content, options = {}) => `<section class="panel ${options.className || ''}"><div class="panel-header"><div><h2 class="panel-title"><span class="title-mark"></span>${esc(title)}</h2>${options.subtitle ? `<p class="panel-subtitle">${esc(options.subtitle)}</p>` : ''}</div>${options.action || ''}</div><div class="panel-body ${options.flush ? 'flush' : ''}">${content}</div></section>`;
  const refresh = '<button class="button button-quiet button-small" data-action="refresh">↻ Refresh</button>';
  const kv = (label, value) => `<div class="detail-item"><dt>${esc(label)}</dt><dd>${value}</dd></div>`;
  const noSession = (thing) => empty('Operator session required', `Accetta la connessione nella pagina di benvenuto iniziale per ${thing}. La sessione rimane in memoria per questa scheda e non viene salvata in locale.`, '▣');
  function setApprovalCount(count) {
    const node = $('#approval-count');
    if (!node) return;
    node.hidden = !count;
    node.textContent = String(count);
  }

  // Budget for a single System 1 probe. Critical workflows get the short 1s budget from
  // settings; ordinary polling keeps the general request budget.
  function criticalProbeTimeoutMs() {
    if (!state.criticalWorkflow) return 12_000;
    try {
      const saved = localStorage.getItem('laya-alert-timeout-ms');
      if (saved) return Math.max(250, Math.min(10_000, Number(saved)));
    } catch {}
    return 1000;
  }

  // --- Real-time Services Health Check & Polling ---
  function updateServicesHealthUI() {
    if (!state.consented) return;
    const { backend, system1, overall } = state.servicesHealth;

    const dotClass = (status) => {
      if (status === 'online') return 'health-dot dot-green';
      if (status === 'offline') return 'health-dot dot-red';
      return 'health-dot dot-yellow';
    };

    if (elements.overallDot) elements.overallDot.className = dotClass(overall === 'all_operational' ? 'online' : overall === 'offline' ? 'offline' : 'checking');
    if (elements.overallLabel) {
      if (overall === 'all_operational') elements.overallLabel.textContent = 'Tutti i servizi operativi';
      else if (overall === 'partial') elements.overallLabel.textContent = 'Connessione parziale';
      else if (overall === 'offline') elements.overallLabel.textContent = 'Servizi offline';
      else elements.overallLabel.textContent = 'Verifica in corso...';
    }

    if (elements.backendDot) elements.backendDot.className = dotClass(backend.status);
    if (elements.backendStatusText) {
      elements.backendStatusText.textContent = backend.status === 'online' ? 'Online' : backend.status === 'offline' ? 'Offline' : 'Errore';
    }
    if (elements.backendLatencyText) {
      elements.backendLatencyText.textContent = backend.latency_ms != null ? `(${backend.latency_ms} ms)` : '';
    }
    const bePill = $('#backend-health-pill');
    if (bePill) {
      const timeStr = backend.checked_at ? fmtTimeOnly(backend.checked_at) : '';
      bePill.title = `Backend FastAPI: ${backend.status} — ${backend.message}${timeStr ? ` (controllo: ${timeStr})` : ''}`;
    }

    if (elements.system1Dot) elements.system1Dot.className = dotClass(system1.status);
    if (elements.system1StatusText) {
      elements.system1StatusText.textContent = system1.status === 'online' ? 'Online' : system1.status === 'offline' ? 'Offline' : 'Errore';
    }
    if (elements.system1LatencyText) {
      elements.system1LatencyText.textContent = system1.latency_ms != null ? `(${system1.latency_ms} ms)` : '';
    }
    const s1Pill = $('#system1-health-pill');
    if (s1Pill) {
      const timeStr = system1.checked_at ? fmtTimeOnly(system1.checked_at) : '';
      s1Pill.title = `Laya System 1: ${system1.status} — ${system1.message}${timeStr ? ` (controllo: ${timeStr})` : ''}`;
    }
  }

  // --- Gestione Allerta Prioritaria e Protezione Workflow Critico ---
  function showPriorityAlert({ workflow, executionId, reason, stopStatus }) {
    state.alertActive = true;
    state.alertAcknowledged = false;
    const nowStr = fmtTime(new Date(), true);

    if (elements.priorityAlertBanner) elements.priorityAlertBanner.hidden = false;
    if (elements.alertWorkflowName) elements.alertWorkflowName.textContent = workflow ? `${shortId(workflow.workflow_id, 10)} (${workflow.project_id || 'unscoped'})` : 'Operazione diretta';
    if (elements.alertExecutionId) elements.alertExecutionId.textContent = executionId || (workflow?.steps?.find((s) => s.execution_id)?.execution_id) || 'N/A';
    if (elements.alertDetectedTime) elements.alertDetectedTime.textContent = nowStr;
    if (elements.alertSystem1State) elements.alertSystem1State.textContent = 'Offline (connessione rifiutata / non raggiungibile)';
    if (elements.alertWorkflowPhase) elements.alertWorkflowPhase.textContent = workflow?.pending_step || 'Esecuzione step critico';
    if (elements.alertReason) elements.alertReason.textContent = reason || 'System 1 non risponde durante un workflow ad alto rischio.';
    if (elements.alertStopStatus) elements.alertStopStatus.textContent = stopStatus;

    if (elements.alertResumeWorkflow) {
      elements.alertResumeWorkflow.hidden = true; // Mostrato solo dopo ripristino verificato
    }

    // Avvia ripetizione sonora
    startAlertSoundLoop();
  }

  function hidePriorityAlert() {
    state.alertActive = false;
    state.alertAcknowledged = false;
    stopAlertSoundLoop();
    if (elements.priorityAlertBanner) elements.priorityAlertBanner.hidden = true;
  }

  async function handleCriticalSystem1Failure() {
    state.consecutiveSystem1Failures += 1;
    let failureThreshold = 2; // default: 2 fallimenti consecutivi
    try {
      const saved = localStorage.getItem('laya-alert-threshold');
      if (saved) failureThreshold = Math.max(1, Number(saved));
    } catch {}

    if (state.consecutiveSystem1Failures < failureThreshold) {
      return; // Errore temporaneo, attende conferma
    }

    // Se c'è un workflow critico attivo in esecuzione o in attesa di approvazione
    const wf = state.criticalWorkflow;
    if (!wf) return;

    let stopConfirmed = false;
    let stopStatusText = 'Interruzione non confermata';

    try {
      // 1. Arresto sicuro immediato tramite pausa nel backend
      const query = wf.project_id ? `?project_id=${encodeURIComponent(wf.project_id)}` : '';
      const pauseResult = await api.post(`/operator/workflows/${encodeURIComponent(wf.workflow_id)}/pause${query}`);
      if (pauseResult && (pauseResult.status === 'paused' || pauseResult.status === 'awaiting_approval')) {
        stopConfirmed = true;
        stopStatusText = 'Arresto confermato (messo in pausa di sicurezza)';
      }
    } catch (err) {
      stopStatusText = 'Interruzione non confermata — errore blocco: ' + formatErrorMessage(err);
    }

    // 2. Registrazione evento di audit per la sicurezza
    try {
      await api.post('/operator/critical-alert/event', {
        event_type: 'system1_failure_interruption',
        workflow_id: wf.workflow_id,
        execution_id: wf.steps?.find((s) => s.execution_id)?.execution_id || null,
        project_id: wf.project_id || null,
        status: stopConfirmed ? 'paused_safely' : 'stop_unconfirmed',
        details: {
          system1_status: state.servicesHealth.system1.status,
          consecutive_failures: state.consecutiveSystem1Failures,
          stop_confirmed: stopConfirmed,
        },
      });
    } catch {
      // Audit failure non deve interrompere l'avviso per l'operatore
    }

    // 3. Attiva allerta visiva e sonora prioritaria
    showPriorityAlert({
      workflow: wf,
      executionId: wf.steps?.find((s) => s.execution_id)?.execution_id,
      reason: 'System 1 offline durante un workflow critico. Nuove operazioni bloccate.',
      stopStatus: stopStatusText,
    });
  }

  function handleSystem1Recovery() {
    state.consecutiveSystem1Failures = 0;
    if (state.alertActive) {
      // System 1 è tornato online: abilita il pulsante di ripresa controllata
      if (elements.alertSystem1State) elements.alertSystem1State.textContent = 'Ripristinato (Online)';
      if (elements.alertResumeWorkflow) {
        elements.alertResumeWorkflow.hidden = false;
      }
      notify('System 1 è tornato online. Verifica lo stato e riprendi il workflow se sicuro.', 'info');
      // Ferma la ripetizione acustica poiché il servizio è ripristinato
      stopAlertSoundLoop();
    }
  }

  async function checkServicesHealth() {
    if (state.healthCheckInProgress) return;
    if (!state.consented) return;
    state.healthCheckInProgress = true;
    try {
      const res = await api.get('/health/services', undefined, { timeoutMs: criticalProbeTimeoutMs() });
      if (res && res.backend && res.system1) {
        state.servicesHealth = {
          backend: res.backend,
          system1: res.system1,
          overall: res.overall_status,
        };

        // Valutazione System 1 durante workflow critici
        if (res.system1.status === 'offline' || res.system1.status === 'error') {
          if (state.criticalWorkflow) {
            await handleCriticalSystem1Failure();
          }
        } else if (res.system1.status === 'online') {
          handleSystem1Recovery();
        }
      }
    } catch (err) {
      const nowIso = new Date().toISOString();
      const isTimeout = err instanceof ApiError && err.code === 'timeout';
      state.servicesHealth = {
        backend: {
          status: 'offline',
          message: isTimeout ? 'Timeout richiesta backend' : 'Backend non raggiungibile (connessione rifiutata)',
          latency_ms: null,
          checked_at: nowIso,
        },
        system1: {
          status: 'offline',
          message: 'Stato non verificabile (backend offline)',
          latency_ms: null,
          checked_at: nowIso,
        },
        overall: 'offline',
      };
      if (state.criticalWorkflow) {
        await handleCriticalSystem1Failure();
      }
    } finally {
      state.healthCheckInProgress = false;
      updateServicesHealthUI();
    }
  }

  // Monitora la presenza di workflow attivi e rileva se sono critici
  async function inspectActiveWorkflows() {
    if (!state.consented || !state.token) return;
    try {
      const workflows = await api.get('/operator/workflows?limit=10');
      // Trova se c'è un workflow running o awaiting_approval che sia critico
      const critical = workflows.find((w) =>
        ['running', 'created', 'awaiting_approval'].includes(w.status) && (w.is_critical || w.steps?.some((s) => s.tool_id?.includes('replace') || s.tool_id?.includes('rollback')))
      );
      state.criticalWorkflow = critical || null;
      setupHealthPolling(); // Adatta la frequenza di polling se il workflow è critico
    } catch {
      // Ignora errori minori nel controllo dei workflow attivi
    }
  }

  function setupHealthPolling() {
    if (state.healthTimer) clearInterval(state.healthTimer);
    state.healthTimer = null;

    let intervalSec = 15; // default ordinario: 15s
    if (state.criticalWorkflow) {
      // Workflow critico attivo: monitoraggio ad alta frequenza (default 2s)
      intervalSec = 2;
      try {
        const savedCrit = localStorage.getItem('laya-alert-critical-interval');
        if (savedCrit) intervalSec = Math.max(1, Number(savedCrit));
      } catch {}
    } else {
      try {
        const saved = localStorage.getItem('laya-health-interval');
        if (saved !== null) intervalSec = Number(saved);
      } catch {}
    }

    if (intervalSec > 0) {
      state.healthTimer = setInterval(() => {
        if (!document.hidden && state.consented) {
          checkServicesHealth();
        }
      }, intervalSec * 1000);
    }
  }

  // --- Transizioni tra Schermata di Benvenuto e Dashboard ---
  function showWelcomeScreen() {
    state.consented = false;
    state.token = '';
    state.projects = [];
    state.projectId = '';
    state.criticalWorkflow = null;
    hidePriorityAlert();
    if (state.healthTimer) {
      clearInterval(state.healthTimer);
      state.healthTimer = null;
    }
    syncProjectSelector();
    setConnection(false, 'Sessione disconnessa');
    if (elements.appShell) elements.appShell.hidden = true;
    if (elements.welcomeScreen) elements.welcomeScreen.hidden = false;
    if (elements.welcomeCard) elements.welcomeCard.hidden = false;
    if (elements.welcomeExitCard) elements.welcomeExitCard.hidden = true;
    if (elements.welcomeStatusBox) elements.welcomeStatusBox.hidden = true;
    if (elements.welcomeErrorDetail) elements.welcomeErrorDetail.hidden = true;
    if (elements.consentConnect) {
      elements.consentConnect.disabled = false;
      elements.consentConnect.textContent = 'Accetta e connetti';
    }
  }

  function showDashboardScreen() {
    state.consented = true;
    if (elements.welcomeScreen) elements.welcomeScreen.hidden = true;
    if (elements.appShell) elements.appShell.hidden = false;
    initAudioContext(); // Attiva il contesto audio al consenso esplicito dell'utente
    syncProjectSelector();
    setConnection(true, 'Backend session active');
    setupHealthPolling();
    checkServicesHealth();
    inspectActiveWorkflows();
  }

  function showExitScreen() {
    state.consented = false;
    state.token = '';
    hidePriorityAlert();
    if (elements.welcomeCard) elements.welcomeCard.hidden = true;
    if (elements.welcomeExitCard) elements.welcomeExitCard.hidden = false;
    setConnection(false, 'Connessione interrotta');
  }

  function setWelcomeConnecting(isConnecting, statusMessage = 'Connessione in corso...', errorMessage = '') {
    state.connecting = isConnecting;
    if (elements.consentConnect) {
      elements.consentConnect.disabled = isConnecting;
      elements.consentConnect.textContent = isConnecting ? 'Connessione...' : 'Accetta e connetti';
    }
    if (elements.consentExit) {
      elements.consentExit.disabled = isConnecting;
    }
    if (elements.welcomeStatusBox) {
      elements.welcomeStatusBox.hidden = !isConnecting && !errorMessage;
    }
    if (elements.welcomeSpinner) {
      elements.welcomeSpinner.hidden = !isConnecting;
    }
    if (elements.welcomeStatusText) {
      elements.welcomeStatusText.textContent = statusMessage;
    }
    if (elements.welcomeErrorDetail) {
      if (errorMessage) {
        elements.welcomeErrorDetail.textContent = errorMessage;
        elements.welcomeErrorDetail.hidden = false;
      } else {
        elements.welcomeErrorDetail.hidden = true;
      }
    }
  }

  // Flusso di connessione automatica dopo consenso esplicito
  async function performAutomaticConnection() {
    setWelcomeConnecting(true, 'Verifica disponibilità backend...');
    try {
      // 1. Verifica raggiungibilità backend locale
      try {
        await api.get('/health');
      } catch (healthErr) {
        throw new ApiError(0, 'backend_unreachable', 'Backend non raggiungibile. Assicurati che Laya Pro sia in esecuzione (scripts/run_backend.ps1).');
      }

      // 2. Verifica stato di System 1
      setWelcomeConnecting(true, 'Verifica stato System 1 e configurazione...');
      let statusConfig = null;
      try {
        statusConfig = await api.get('/status');
      } catch (statusErr) {
        throw new ApiError(statusErr.status || 500, 'system_status_failed', `Impossibile verificare lo stato dei sistemi: ${statusErr.message}`);
      }

      if (statusConfig && !statusConfig.system1_configured) {
        notify('System 1 non configurato localmente; le funzionalitàà decisionali opereranno in modalità limitata.', 'info');
      }

      // 3. Inizializzazione sessione operatore sicura (loopback consent session)
      setWelcomeConnecting(true, 'Inizializzazione sessione operatore locale...');
      let sessionData = null;
      try {
        sessionData = await api.post('/operator/session/connect', {});
      } catch (sessionErr) {
        if (sessionErr.status === 403) {
          throw new ApiError(403, 'cross_origin_blocked', 'Origine non valida. La sessione locale può essere aperta solo da loopback.');
        }
        if (sessionErr.status === 503) {
          throw new ApiError(503, 'operator_auth_not_configured', 'Il backend non ha configurato LAYA_APPROVAL_TOKEN per le funzionalitàà operatore.');
        }
        throw new ApiError(sessionErr.status || 401, 'session_unauthorized', `Inizializzazione sessione operatore non riuscita: ${sessionErr.message}`);
      }

      if (!sessionData || !sessionData.session_token) {
        throw new ApiError(0, 'invalid_session_response', 'Il backend non ha restituito un token di sessione valido.');
      }

      state.token = sessionData.session_token;

      // 4. Caricamento dati di stato e progetti autorizzati
      setWelcomeConnecting(true, 'Caricamento stato operativo e progetti autorizzati...');
      try {
        const [verifiedStatus, projects] = await Promise.all([
          api.get('/operator/status'),
          api.get('/operator/projects'),
        ]);
        if (!Array.isArray(projects) || projects.some((p) => typeof p !== 'string')) {
          throw new ApiError(0, 'invalid_project_response', 'Il backend ha restituito un elenco progetti non valido.');
        }
        state.projects = [...new Set(projects)];
        state.lastData.status = verifiedStatus;
      } catch (projErr) {
        state.token = '';
        throw new ApiError(projErr.status || 0, 'projects_load_failed', `Errore durante il caricamento dei progetti: ${projErr.message}`);
      }

      setWelcomeConnecting(false);
      showDashboardScreen();
      notify('Connessione locale stabilita con successo.', 'success');
      await render();
    } catch (err) {
      const errMsg = formatErrorMessage(err);
      setWelcomeConnecting(false, 'Connessione fallita', errMsg);
      notify(errMsg, 'error');
    }
  }

  async function disconnectOperatorSession(reason = 'Sessione terminata.') {
    if (state.token) {
      try {
        await api.post('/operator/session/disconnect', {});
      } catch {}
    }
    state.token = '';
    state.projects = [];
    state.projectId = '';
    state.criticalWorkflow = null;
    hidePriorityAlert();
    setApprovalCount(0);
    showWelcomeScreen();
    notify(reason, 'info');
  }

  async function render() {
    if (!state.consented) {
      showWelcomeScreen();
      return;
    }
    const [routePath = '', routeQuery = ''] = location.hash.replace(/^#\/?/, '').split('?', 2);
    const decodeRoutePart = (part) => { try { return decodeURIComponent(part); } catch { return part; } };
    const route = routePath.split('/').filter(Boolean).map(decodeRoutePart);
    const page = route[0] || 'overview';
    const workflowId = page === 'workflow' ? route[1] : null;
    const executionId = page === 'execution' ? route[1] : null;
    state.page = workflowId ? 'workflow' : executionId ? 'execution' : page;
    const titles = { overview: 'Overview', chat: 'AI Chat', approvals: 'Approval center', workflows: 'Workflows', workflow: 'Workflow details', executions: 'Execution history', execution: 'Execution details', status: 'System status', settings: 'Settings' };
    if ($('#topbar-page')) $('#topbar-page').textContent = titles[state.page] || 'Overview';
    syncProjectSelector();
    document.querySelectorAll('[data-nav]').forEach((a) => {
      const active = a.dataset.nav === state.page || (state.page === 'workflow' && a.dataset.nav === 'workflows') || (state.page === 'execution' && a.dataset.nav === 'executions');
      a.classList.toggle('active', active);
      active ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current');
    });
    state.listeners.abort();
    state.listeners = new AbortController();
    if (elements.sidebar) elements.sidebar.classList.remove('open');
    if ($('#mobile-menu')) $('#mobile-menu').setAttribute('aria-expanded', 'false');
    if (elements.view) {
      elements.view.setAttribute('aria-busy', 'true');
      elements.view.innerHTML = loading();
    }
    try {
      if (workflowId) await renderWorkflow(workflowId);
      else if (executionId) await renderExecution(executionId);
      else if (page === 'approvals') await renderApprovals(routeQuery);
      else if (page === 'workflows') await renderWorkflows();
      else if (page === 'executions') await renderExecutions();
      else if (page === 'status') await renderStatus();
      else if (page === 'settings') await renderSettings();
      else if (page === 'chat') await renderChat(); else if (page === 'memory') await renderMemory(); else await renderOverview();
    } catch (error) {
      if (elements.view) {
        elements.view.innerHTML = heading('LOCAL CONTROL PLANE', titles[state.page] || 'Overview', 'Showing data returned by the local backend only.') + errorPanel(error);
      }
      if (error instanceof ApiError && error.status === 0) setConnection(false, 'Backend non raggiungibile');
    } finally {
      if (elements.view) elements.view.setAttribute('aria-busy', 'false');
      bindPage();
    }
  }

  async function statusData() {
    await api.get('/health');
    const result = await api.get('/operator/status');
    state.lastData.status = result;
    setConnection(true, 'Backend session active');
    return result;
  }
  async function approvalsData(limit = 100) {
    const result = await api.get(`/operator/approvals?limit=${limit}`);
    state.lastData.approvals = result;
    setApprovalCount(result.filter((item) => item.approval.status === 'pending' && Date.parse(item.approval.expires_at) > Date.now()).length);
    return result;
  }
  const metric = (name, value, text, symbol) => `<article class="metric-card"><div class="metric-top"><span>${esc(name)}</span><span class="metric-glyph" aria-hidden="true">${esc(symbol)}</span></div><div class="metric-value">${esc(value)}</div><div class="metric-sub">${esc(text)}</div></article>`;
  const serviceRow = (name, data, copy) => `<div class="service-row"><div class="service-name"><span class="service-icon">${name.includes('2') ? '✦' : '◉'}</span><div><strong>${esc(name)}</strong><small>${esc(copy)}${data?.last_error_code ? ` · ${esc(data.last_error_code)}` : ''}</small></div></div><div class="service-meta">${badge(data?.status || 'unknown')}<small>${data?.last_latency_ms == null ? 'No latency' : `${data.last_latency_ms} ms`}</small></div></div>`;

  async function renderOverview() {
    if (!state.token) {
      try { await api.get('/health'); setConnection(true, 'Backend raggiungibile · consenso necessario'); }
      catch { setConnection(false, 'Backend non raggiungibile · sessione non connessa'); }
      elements.view.innerHTML = heading('LOCAL CONTROL PLANE', 'Operator overview', 'Monitor backend-recorded approvals, workflows, invocations and configured adapters.', '<button class="button button-primary" data-action="connect">Accetta e connetti</button>') + noSession('visualizzare la panoramica');
      return;
    }
    const [status, approvals, workflows, executions] = await Promise.all([statusData(), approvalsData(12), api.get('/operator/workflows?limit=8'), api.get('/operator/executions?limit=6&offset=0')]);
    const pending = approvals.filter((a) => a.approval.status === 'pending' && Date.parse(a.approval.expires_at) > Date.now());
    const active = workflows.filter((w) => ['created','running','paused','awaiting_approval'].includes(w.status));
    const failed = Object.entries(status.execution_counts || {}).filter(([s]) => ['failed','timed_out','cancelled'].includes(s)).reduce((n, [,v]) => n + v, 0);
    const approvalBody = pending.length ? approvalTable(pending.slice(0, 5)) : empty('No pending approvals', 'No pending and unexpired approvals are currently recorded.', '✓');
    const executionBody = executions.items.length ? executionTable(executions.items, true) : empty('No verified execution records', 'Execution history appears only after actual backend tool invocations.');
    elements.view.innerHTML = heading('LOCAL CONTROL PLANE', 'Operator overview', 'A verified operational snapshot. Model configuration, runtime observations, authorization and tool executions remain distinct.', `${refresh} <button class="button button-primary" data-action="goto-approvals">Review approvals${pending.length ? ` · ${pending.length}` : ''}</button>`)
      + `<div class="metric-grid">${metric('Pending approvals', pending.length, 'Unexpired backend records', '◎')}${metric('Active workflows', active.length, 'Created, running or paused', '⌘')}${metric('Executions recorded', executions.total, 'Durable metadata only', '↗')}${metric('Failed / cancelled', failed, 'Backend lifecycle states', '!')}</div>`
      + `<div class="panel-grid">${panel('Backend and adapter status', `<div class="service-stack"><div class="service-row"><div class="service-name"><span class="service-icon">⌁</span><div><strong>API process</strong><small>Control request ${esc(fmtTime(status.observed_at))} · health checked ${esc(fmtTime(status.last_successful_health_check))}</small></div></div>${badge(status.api_status)}</div>${serviceRow('System 1 · Laya', status.system1, 'Native runtime unverified')}${serviceRow('System 2 · plain text', status.system2, 'Optional and non-authoritative')}</div><div class="notice notice-warn" style="margin-top:12px"><span class="notice-icon">i</span><span>Adapter request observations do not certify native runtime compatibility. System 2 has no authorization or executor access.</span></div>`, { subtitle: 'Status returned by the backend' })}${panel('Approval queue', approvalBody, { subtitle: `${pending.length} pending and valid · backend scope remains authoritative`, flush: true, action: '<a href="#approvals" class="text-link">Open center →</a>' })}</div>`
      + `<div class="panel-grid" style="margin-top:16px">${panel('Workflow activity', workflows.length ? workflowTable(workflows, true) : empty('No workflow records', 'No workflows have been persisted.'), { flush: true, action: '<a class="text-link" href="#workflows">All workflows →</a>' })}${panel('Recent tool executions', executionBody, { flush: true, action: '<a class="text-link" href="#executions">History →</a>' })}</div>`;
  }

  function approvalRow(detail) {
    const a = detail.approval; const preview = detail.parameters_preview || a.parameters_preview || {};
    const operation = detail.operation_id || preview.operation_id || preview.path || a.tool_id;
    const workflow = a.workflow_id ? `<a class="text-link" href="#workflow/${encodeURIComponent(a.workflow_id)}">${esc(shortId(a.workflow_id, 8))}</a>` : '<span class="muted">Direct action</span>';
    return `<tr><td><a class="table-primary mini-id" href="#approvals?id=${encodeURIComponent(a.approval_id)}">${esc(shortId(a.approval_id, 9))}</a><span class="table-secondary">${esc(a.tool_id)}</span></td><td><span class="table-primary">${esc(operation)}</span><span class="table-secondary">${esc(a.project_id || 'No project scope')}</span></td><td>${workflow}</td><td>${badge(a.status)}<span class="table-secondary">${badge(detail.validation.status)}</span></td><td>${esc(fmtTime(a.expires_at))}</td><td><button class="button button-quiet button-small" data-action="view-approval" data-approval="${esc(a.approval_id)}">Review</button></td></tr>`;
  }
  function approvalTable(records) {
    if (!records.length) return empty('No approvals in this view', 'The backend returned no approval records matching this filter.', '◎');
    return `<div class="table-wrap"><table><thead><tr><th>Request / tool</th><th>Operation / project</th><th>Workflow</th><th>Status / validation</th><th>Expires</th><th>Details</th></tr></thead><tbody>${records.map(approvalRow).join('')}</tbody></table></div>`;
  }
  async function renderApprovals(routeQuery = '') {
    if (!state.token) { elements.view.innerHTML = heading('HUMAN IN THE LOOP', 'Approval center', 'Inspect backend-supplied action previews, scope and expiration.') + noSession('elencare le approvazioni'); return; }
    const all = await approvalsData(200); const filtered = all.filter((a) => state.approvalFilter === 'all' || a.approval.status === state.approvalFilter);
    const filters = ['all','pending','approved','rejected','expired','consumed'];
    const chips = `<div class="filter-row" style="margin-bottom:12px">${filters.map((f) => `<button class="filter-chip ${state.approvalFilter === f ? 'active' : ''}" data-action="approval-filter" data-filter="${f}" aria-pressed="${state.approvalFilter === f}">${f.replaceAll('_',' ')} (${f === 'all' ? all.length : all.filter((a) => a.approval.status === f).length})</button>`).join('')}</div>`;
    const selectedId = new URLSearchParams(routeQuery).get('id');
    elements.view.innerHTML = heading('HUMAN IN THE LOOP', 'Approval center', 'Validation summaries are backend-derived. Final status, scope, expiry, fingerprint and single-use are always checked again server-side.', refresh)
      + `<div class="notice notice-warn" style="margin-bottom:14px"><span class="notice-icon">!</span><span>Review the project and safe parameter preview. File contents and credential-like fields are redacted or omitted. Approval records an authorization decision; it does not execute the operation.</span></div>`
      + panel('Approval records', `${chips}${approvalTable(filtered)}`, { subtitle: `${filtered.length} backend approval records`, flush: false });
    if (selectedId) await renderApprovalDetail(selectedId);
  }
  const detail = (label, content) => `<div class="detail-item"><dt>${esc(label)}</dt><dd>${content}</dd></div>`;
  async function renderApprovalDetail(id) {
    let host = $('#approval-detail-panel');
    if (!host) { elements.view.insertAdjacentHTML('beforeend', `<div id="approval-detail-panel" style="margin-top:16px"></div>`); host = $('#approval-detail-panel'); }
    host.innerHTML = loading();
    try {
      const data = await api.get(`/operator/approvals/${encodeURIComponent(id)}`);
      const a = data.approval; const preview = data.parameters_preview || a.parameters_preview || {};
      const valid = data.validation?.status || 'unknown';
      const expired = Date.parse(a.expires_at) <= Date.now();
      const controls = a.status === 'pending' && !expired && !['invalid','expired','unavailable'].includes(valid)
        ? `<div class="table-actions"><button class="button button-success button-small" data-action="approve" data-busy-key="approve:${esc(id)}" data-approval="${esc(id)}">Approve</button><button class="button button-quiet button-small" data-action="reject" data-busy-key="reject:${esc(id)}" data-approval="${esc(id)}">Reject</button></div>`
        : '<span class="muted">No decision action available</span>';
      const validationWarning = expired || valid === 'expired'
        ? '<div class="notice notice-danger" style="margin-top:13px"><span class="notice-icon">!</span><span>This approval has expired. The backend will reject any attempt to use it.</span></div>'
        : valid === 'invalid' || valid === 'unavailable'
          ? '<div class="notice notice-danger" style="margin-top:13px"><span class="notice-icon">!</span><span>This approval is invalid or unavailable for the current operation. Do not approve or execute it.</span></div>'
          : valid === 'backend_recorded'
            ? '<div class="notice notice-warn" style="margin-top:13px"><span class="notice-icon">!</span><span>Full parameters are not retained. Exact action fingerprint and scope will be checked by the backend again before use.</span></div>'
            : '';
      const workflow = a.workflow_id ? `<a class="text-link" href="#workflow/${encodeURIComponent(a.workflow_id)}">Open workflow ${esc(shortId(a.workflow_id, 9))} →</a>` : '<span class="muted">No associated workflow</span>';
      const fields = `<dl class="detail-list">${detail('Approval ID', `<span class="mono">${esc(a.approval_id)}</span>`)}${detail('Tool / operation', `${esc(a.tool_id)}${data.operation_id ? `<br><span class="mono">${esc(data.operation_id)}</span>` : ''}`)}${detail('Workflow', workflow)}${detail('Project scope', esc(a.project_id || 'None'))}${detail('Expiration', esc(fmtTime(a.expires_at, true)))}${detail('Status', badge(a.status))}${detail('Requested by', esc(a.requested_by))}${detail('Decision by', esc(a.decided_by || 'Not decided'))}${detail('Fingerprint validation', `${esc(valid)} · checked by backend`)}${detail('Reason', esc(a.reason))}</dl>`;
      host.innerHTML = panel('Approval details', `<div class="detail-grid"><div>${fields}<h3 class="settings-section-title">Normalized safe parameters</h3><pre class="code-block">${esc(JSON.stringify(preview, null, 2))}</pre></div><div><div>${badge(valid)}</div><p class="heading-copy" style="margin-top:10px">${esc(data.validation?.message || 'Backend did not provide validation details.')}</p><div class="notice notice-warn" style="margin-top:13px"><span class="notice-icon">!</span><span>Frontend display cannot authorize execution. Server repeats exact validation at use time.</span></div>${validationWarning}<div style="margin-top:16px">${controls}</div></div></div>`, { subtitle: `Fetched ${fmtTime(new Date())} · backend response shown` });
    } catch (error) { host.innerHTML = errorPanel(error); }
  }

  function workflowTable(items, compact) {
    const current = state.workflowFilter || 'all'; const records = current === 'all' ? items : items.filter((w) => w.status === current);
    if (!records.length) return empty('No matching workflows', 'No workflow record returned by the backend matches this view.', '⌘');
    return `<div class="table-wrap"><table><thead><tr><th>Workflow</th><th>Status</th><th>Progress</th><th>Updated</th><th>Details</th></tr></thead><tbody>${records.map((w) => `<tr><td><a class="table-primary mini-id" href="#workflow/${encodeURIComponent(w.workflow_id)}">${esc(shortId(w.workflow_id, 9))}</a><span class="table-secondary">${esc(w.project_id || 'No project')}${w.is_critical ? ' · ⚠️ Critico' : ''}</span></td><td>${badge(w.status)}</td><td>${esc(`${w.completed_steps?.length || 0}/${w.steps?.length || 0} steps`)}${w.error_code ? `<span class="table-secondary">${esc(w.error_code)}</span>` : ''}</td><td>${esc(fmtTime(w.updated_at))}</td><td><a class="text-link" href="#workflow/${encodeURIComponent(w.workflow_id)}">${compact ? 'Open →' : 'View →'}</a></td></tr>`).join('')}</tbody></table></div>`;
  }
  async function renderWorkflows() {
    if (!state.token) { elements.view.innerHTML = heading('ORCHESTRATION', 'Workflows', 'Review backend-persisted workflow definitions and lifecycle state.') + noSession('visualizzare i workflow'); return; }
    const records = await api.get('/operator/workflows?limit=200'); state.lastData.workflows = records;
    const filters = ['all','created','running','awaiting_approval','paused','completed','failed','cancelled'];
    const chips = `<div class="filter-row" style="padding:12px 14px">${filters.map((f) => `<button class="filter-chip ${(state.workflowFilter || 'all') === f ? 'active' : ''}" data-action="workflow-filter" data-filter="${f}">${f.replaceAll('_',' ')}</button>`).join('')}</div>`;
    elements.view.innerHTML = heading('ORCHESTRATION', 'Workflows', 'Step state and transitions are read from SQLite-backed workflow records. Resume goes through existing backend approval checks.', refresh) + panel('Workflow records', `${workflowTable(records, false)}${chips}`, { subtitle: `${records.length} records returned · workflow output is metadata only`, flush: true });
  }
  async function renderWorkflow(id) {
    if (!state.token) { elements.view.innerHTML = heading('WORKFLOW RECORD', 'Workflow details', 'Authenticated backend data only.') + noSession('visualizzare questo workflow'); return; }
    const w = await api.get(`/operator/workflows/${encodeURIComponent(id)}`); state.lastData.workflow = w;
    const hasApproval = Boolean(w.pending_step);
    const pendingApproval = w.steps?.find((step) => step.step_id === w.pending_step);
    const pendingApprovalUsable = pendingApproval?.approval_status === 'approved'
      && Date.parse(pendingApproval.approval_expires_at || '') > Date.now();
    const canResume = ['created','paused','awaiting_approval'].includes(w.status)
      && (!w.pending_step || pendingApprovalUsable);
    const actions = `${refresh} ${['created','running','paused','awaiting_approval'].includes(w.status) ? `<button class="button button-quiet" data-action="pause-workflow" data-busy-key="pause-workflow:${esc(id)}" data-workflow="${esc(id)}" ${w.status === 'paused' ? 'disabled' : ''}>Pause</button>${canResume ? `<button class="button button-primary" data-action="resume-workflow" data-busy-key="resume-workflow:${esc(id)}" data-workflow="${esc(id)}">Resume</button>` : ''}<button class="button button-danger" data-action="cancel-workflow" data-busy-key="cancel-workflow:${esc(id)}" data-workflow="${esc(id)}">Cancel</button>` : ''}`;
    const steps = w.steps?.map((s, i) => `<div class="workflow-step"><span class="step-index">${i + 1}</span><div class="workflow-step-copy"><strong>${esc(s.step_id)} · ${esc(s.tool_id)}</strong><small>${s.depends_on?.length ? `Depends on ${esc(s.depends_on.join(', '))}` : 'No dependencies'}${s.execution_id ? ` · execution ${esc(shortId(s.execution_id))}` : ''}${s.approval_id ? ` · <a class="text-link" href="#approvals?id=${encodeURIComponent(s.approval_id)}">approval ${esc(shortId(s.approval_id))} (${esc(s.approval_status || 'unknown')}${s.approval_expires_at ? ` · expires ${esc(fmtTime(s.approval_expires_at))}` : ''})</a>` : ''}</small></div>${badge(s.status)}</div>`).join('') || empty('No steps', 'The backend returned no workflow steps.');
    const events = w.events?.length ? '<div class="event-list">' + w.events.map((e) => `<div class="event-row"><span class="event-mark"></span><div><strong>${esc(e.event_type.replaceAll('.', ' · '))}</strong><small>${esc(fmtTime(e.created_at, true))}${e.resource_id ? ` · ${esc(shortId(e.resource_id))}` : ''}</small></div>${badge(e.status)}</div>`).join('') + '</div>' : empty('No transition events returned', 'The audit API returned no workflow-linked event metadata.');
    elements.view.innerHTML = heading('WORKFLOW RECORD', `Workflow ${shortId(id, 11)}`, `Project ${w.project_id || 'unscoped'} · created ${fmtTime(w.created_at)} · updated ${fmtTime(w.updated_at)}`, actions)
      + (hasApproval ? '<div class="notice notice-warn" style="margin-bottom:14px"><span class="notice-icon">!</span><span>Pending approval lifecycle is preserved. Resume cannot bypass exact backend approval validation.</span></div>' : '')
      + (w.is_critical ? '<div class="notice notice-danger" style="margin-bottom:14px"><span class="notice-icon">⚠️</span><span>Workflow classificato come CRITICO: protetto da monitoraggio continuo ad alta frequenza di System 1.</span></div>' : '')
      + `<div class="detail-grid">${panel('Steps and state', `${w.error_code ? `<div class="notice notice-danger" style="margin-bottom:12px">Backend error code: ${esc(w.error_code)}</div>` : ''}${steps}`, { subtitle: `${w.completed_steps?.length || 0} of ${w.steps?.length || 0} completed` })}${panel('Workflow metadata', `<dl class="detail-list">${detail('Workflow ID', `<span class="mono">${esc(w.workflow_id)}</span>`)}${detail('Project', esc(w.project_id || 'None'))}${detail('Status', badge(w.status))}${detail('Critico', w.is_critical ? 'Sì (alto rischio)' : 'No')}${detail('Version', esc(w.version))}${detail('Pending step', esc(w.pending_step || 'None'))}${detail('Completed', esc(w.completed_steps?.join(', ') || 'None'))}</dl>`, { subtitle: 'Read-only backend state' })}</div><div style="margin-top:16px">${panel('State and approval events', events, { subtitle: 'Allowlisted audit metadata only' })}</div>`;
  }

  function executionTable(items, compact) {
    if (!items.length) return empty('No verified execution records', 'This backend has not recorded tool invocation history yet. The dashboard does not reconstruct or simulate missing events.', '↗');
    return `<div class="table-wrap"><table><thead><tr><th>Execution / tool</th><th>Workflow / step</th><th>Status</th><th>Started / completed</th><th>Duration</th><th>Details</th></tr></thead><tbody>${items.map((r) => `<tr><td><a class="table-primary mini-id" href="#execution/${encodeURIComponent(r.execution_id)}">${esc(shortId(r.execution_id, 9))}</a><span class="table-secondary">${esc(r.tool_id)}</span></td><td>${r.workflow_id ? `<a class="text-link" href="#workflow/${encodeURIComponent(r.workflow_id)}">${esc(shortId(r.workflow_id, 8))}</a>` : '<span class="muted">Direct</span>'}<span class="table-secondary">${esc(r.step_id || 'No step')}</span></td><td>${badge(r.status)}${r.error_code ? `<span class="table-secondary">${esc(r.error_code)}</span>` : ''}</td><td>${esc(fmtTime(r.started_at))}<span class="table-secondary">${r.completed_at ? `Finished ${esc(fmtTime(r.completed_at))}` : 'Incomplete lifecycle record'}</span></td><td>${esc(fmtDuration(r.duration_ms))}</td><td><a class="text-link" href="#execution/${encodeURIComponent(r.execution_id)}">${compact ? 'Open →' : 'Details →'}</a></td></tr>`).join('')}</tbody></table></div>`;
  }
  async function renderExecutions() {
    if (!state.token) { elements.view.innerHTML = heading('OBSERVABILITY', 'Execution history', 'Search actual, durable tool invocation records.') + noSession('visualizzare la cronologia'); return; }
    const q = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(state.executionOffset) });
    if (state.executionQuery.trim()) q.set('q', state.executionQuery.trim()); if (state.executionStatus) q.set('status', state.executionStatus);
    if (state.executionSince) q.set('since', new Date(`${state.executionSince}T00:00:00Z`).toISOString()); if (state.executionUntil) q.set('until', new Date(`${state.executionUntil}T23:59:59.999Z`).toISOString());
    const result = await api.get(`/operator/executions?${q}`); state.lastData.executions = result;
    const filters = `<div class="toolbar"><label class="field grow"><span class="field-label">Search workflow / execution ID</span><input class="text-input" id="execution-query" maxlength="128" value="${esc(state.executionQuery)}" placeholder="Search identifiers" /></label><label class="field"><span class="field-label">Status</span><select class="select-input" id="execution-status"><option value="">All</option>${['running','succeeded','failed','timed_out','cancelled'].map((v) => `<option value="${v}" ${state.executionStatus === v ? 'selected' : ''}>${v.replaceAll('_',' ')}</option>`).join('')}</select></label><label class="field"><span class="field-label">From UTC</span><input class="text-input" type="date" id="execution-since" value="${esc(state.executionSince)}" /></label><label class="field"><span class="field-label">To UTC</span><input class="text-input" type="date" id="execution-until" value="${esc(state.executionUntil)}" /></label><button class="button button-quiet button-small" data-action="clear-execution">Clear</button></div>`;
    const page = Math.floor(result.offset / PAGE_SIZE) + 1; const max = Math.max(1, Math.ceil(result.total / PAGE_SIZE));
    const pager = `<div class="pagination"><span>${result.total ? `Page ${page} of ${max} · ${result.total} records` : 'No records'}</span><div class="pagination-actions"><button class="button button-quiet button-small" data-action="execution-prev" ${result.offset <= 0 ? 'disabled' : ''}>← Previous</button><button class="button button-quiet button-small" data-action="execution-next" ${result.offset + PAGE_SIZE >= result.total ? 'disabled' : ''}>Next →</button></div></div>`;
    elements.view.innerHTML = heading('OBSERVABILITY', 'Execution history', 'Search, filter and page through metadata persisted by real executor calls. No missing events are reconstructed.', refresh) + panel('Tool invocations', `${filters}<div class="panel-body flush">${executionTable(result.items, false)}</div>${pager}`, { subtitle: `${result.total} matching records`, flush: true });
  }
  async function renderExecution(id) {
    if (!state.token) { elements.view.innerHTML = heading('EXECUTION RECORD', 'Execution details', 'Authenticated backend data only.') + noSession('visualizzare questo dettaglio'); return; }
    const d = await api.get(`/operator/executions/${encodeURIComponent(id)}`); const r = d.execution;
    const events = d.events?.length ? '<div class="event-list">' + d.events.map((e) => `<div class="event-row"><span class="event-mark"></span><div><strong>${esc(e.event_type.replaceAll('.', ' · '))}</strong><small>${esc(fmtTime(e.created_at, true))}</small></div>${badge(e.status)}</div>`).join('') + '</div>' : empty('No linked audit events', 'The backend returned no workflow-linked audit metadata.');
    const linked = d.workflow ? `<a class="text-link" href="#workflow/${encodeURIComponent(d.workflow.workflow_id)}">Open workflow ${esc(shortId(d.workflow.workflow_id, 9))} →</a>` : '<span class="muted">No associated workflow</span>';
    elements.view.innerHTML = heading('EXECUTION RECORD', `Execution ${shortId(id, 11)}`, `${r.tool_id} · started ${fmtTime(r.started_at, true)}`, `${refresh} ${linked}`)
      + `<div class="detail-grid">${panel('Invocation metadata', `<dl class="detail-list">${detail('Execution ID', `<span class="mono">${esc(r.execution_id)}</span>`)}${detail('Request ID', `<span class="mono">${esc(r.request_id)}</span>`)}${detail('Tool / step', `${esc(r.tool_id)}${r.step_id ? ` · ${esc(r.step_id)}` : ''}`)}${detail('Workflow', linked)}${detail('Status', badge(r.status))}${detail('Started / completed', `${esc(fmtTime(r.started_at, true))}<br>${esc(fmtTime(r.completed_at, true))}`)}${detail('Duration', esc(fmtDuration(r.duration_ms)))}${detail('Error code', esc(r.error_code || 'None'))}</dl>${r.error_summary ? `<div class="notice notice-danger" style="margin-top:12px">${esc(r.error_summary)}</div>` : ''}<div class="notice" style="margin-top:12px"><span class="notice-icon">i</span><span>Parameters and tool outputs are deliberately not persisted in execution history.</span></div>`, { subtitle: 'Verified executor record' })}${panel('Workflow steps', d.workflow ? `${detail('Workflow', linked)}${detail('Status', badge(d.workflow.status))}${detail('Project', esc(d.workflow.project_id || 'None'))}${(d.workflow.steps || []).map((s) => `<div class="event-row"><span class="event-mark"></span><div><strong>${esc(s.step_id)}</strong><small>${esc(s.tool_id)}</small></div></div>`).join('')}` : empty('No workflow link', 'This is not associated with a persistent workflow.'), { subtitle: 'Read-only' })}</div><div style="margin-top:16px">${panel('Workflow audit events', events, { subtitle: 'Allowlisted metadata only · no raw audit details' })}</div>`;
  }

  async function renderStatus() {
    if (!state.token) { elements.view.innerHTML = heading('OBSERVABILITY', 'System status', 'Configured runtime status is distinct from actual process health.') + noSession('caricare lo stato operativo'); return; }
    const s = await statusData(); const component = (name, c, explanation) => `<article class="status-card"><div class="status-card-heading"><h3>${esc(name)}</h3>${badge(c?.status || 'unknown')}</div><div class="status-big">${esc(!c?.configured ? 'Not configured' : c.status === 'unknown' ? 'Configured · unchecked' : c.status)}</div><p>${esc(explanation)}</p><div class="status-kv"><span>Attempt</span><span>${esc(fmtTime(c?.last_attempt_at, true))}</span><span>Success</span><span>${esc(fmtTime(c?.last_successful_at, true))}</span><span>Latency</span><span>${c?.last_latency_ms == null ? 'Not measured' : `${c.last_latency_ms} ms`}</span><span>Last error type</span><span>${esc(c?.last_error_code || 'None')}</span><span>Native runtime verified</span><span>${c?.native_runtime_verified ? 'Yes' : 'No'}</span></div></article>`;
    elements.view.innerHTML = heading('OBSERVABILITY', 'System status', 'Status reflects actual API health checks and recent in-process adapter calls; no runtime is probed by this page.', refresh)
      + `<div class="notice notice-warn" style="margin-bottom:14px"><span class="notice-icon">!</span><span>System 2 remains isolated plain-text generation. Generated content is not an authorization signal. Adapter configuration does not imply a native runtime is available.</span></div>`
      + `<div class="status-layout"><div class="status-cards"><article class="status-card"><div class="status-card-heading"><h3>Backend API</h3>${badge(s.api_status)}</div><div class="status-big">Reachable</div><p>This authenticated status response succeeded at ${esc(fmtTime(s.observed_at, true))}.</p><div class="status-kv"><span>Last health request</span><span>${esc(fmtRelative(s.last_successful_health_check))}</span><span>Session</span><span>Operator active</span></div></article>${component('System 1 · Laya adapter', s.system1, 'Normalized decision adapter. Native contract is unverified.')}${component('System 2 · optional text', s.system2, 'Plain text only; not connected to policy or execution.')}</div>${panel('Recorded backend diagnostics', `<dl class="detail-list">${detail('Last successful health check', esc(fmtTime(s.last_successful_health_check, true)))}${detail('Latest audit event', esc(fmtTime(s.latest_audit_event_at, true)))}${detail('Last successful execution', esc(fmtTime(s.last_successful_execution_at, true)))}${detail('Decision records', esc(s.decision_count))}${detail('Pending unexpired approvals', esc(s.pending_approval_count))}${detail('Active workflows', esc(s.active_workflow_count))}</dl>`, { subtitle: 'SQLite-backed metadata counts' })}${panel('Execution outcomes', Object.keys(s.execution_counts || {}).length ? `<div class="filter-row">${Object.entries(s.execution_counts).map(([k,v]) => badge(k, `${k.replaceAll('_',' ')} · ${v}`)).join('')}</div>` : empty('No executions recorded', 'No tool invocation status events have been written yet.'))}</div>`;
  }

  async function renderSettings() {
    const settings = await api.get('/status');
    let refreshValue = 0; try { refreshValue = Number(localStorage.getItem('laya-dashboard-refresh')) || 0; } catch { /* storage blocked */ }
    let healthInterval = 15; try { const h = localStorage.getItem('laya-health-interval'); if (h !== null) healthInterval = Number(h); } catch {}
    let criticalInterval = 2; try { const c = localStorage.getItem('laya-alert-critical-interval'); if (c) criticalInterval = Number(c); } catch {}
    let alertThreshold = 2; try { const t = localStorage.getItem('laya-alert-threshold'); if (t) alertThreshold = Number(t); } catch {}
    let probeTimeout = 1000; try { const p = localStorage.getItem('laya-alert-timeout-ms'); if (p) probeTimeout = Number(p); } catch {}
    let audioEnabled = true; try { const a = localStorage.getItem('laya-alert-audio'); if (a !== null) audioEnabled = a === 'true'; } catch {}
    let visualEnabled = true; try { const v = localStorage.getItem('laya-alert-visual'); if (v !== null) visualEnabled = v === 'true'; } catch {}
    let alertRepeat = 4; try { const r = localStorage.getItem('laya-alert-repeat-sec'); if (r) alertRepeat = Number(r); } catch {}

    elements.view.innerHTML = heading('PREFERENCES', 'Settings', 'Dashboard preferences are stored locally. Backend configuration remains read-only here.', '<button class="button button-quiet button-small" data-action="disconnect">Disconnetti sessione</button>')
      + '<div class="notice" style="margin-bottom:14px"><span class="notice-icon">▣</span><span>La sessione operatore è autorizzata su loopback e conservata esclusivamente nella memoria di questo tab. Backend model URLs, token di approvazione, cartelle progetto e permessi rimangono non modificabili da qui.</span></div>'
      + `<div class="settings-section-title">Monitoraggio e allerte dei workflow critici</div><div class="settings-grid">`
      + `<article class="settings-card"><h3>Polling stato servizi (ordinario e critico)</h3><p>Frequenze di verifica dello stato di Backend e System 1.</p>`
      + `<label class="field-label" for="health-interval">Intervallo di polling ordinario</label><select id="health-interval" class="select-input"><option value="0" ${healthInterval === 0 ? 'selected' : ''}>Sospendi polling</option><option value="5" ${healthInterval === 5 ? 'selected' : ''}>5 secondi</option><option value="10" ${healthInterval === 10 ? 'selected' : ''}>10 secondi</option><option value="15" ${healthInterval === 15 ? 'selected' : ''}>15 secondi (predefinito)</option><option value="30" ${healthInterval === 30 ? 'selected' : ''}>30 secondi</option><option value="60" ${healthInterval === 60 ? 'selected' : ''}>60 secondi</option></select>`
      + `<label class="field-label" for="critical-interval" style="margin-top:10px">Intervallo durante workflow critici</label><select id="critical-interval" class="select-input"><option value="1" ${criticalInterval === 1 ? 'selected' : ''}>1 secondo</option><option value="2" ${criticalInterval === 2 ? 'selected' : ''}>2 secondi (predefinito)</option><option value="3" ${criticalInterval === 3 ? 'selected' : ''}>3 secondi</option><option value="5" ${criticalInterval === 5 ? 'selected' : ''}>5 secondi</option></select>`
      + `<label class="field-label" for="alert-threshold" style="margin-top:10px">Controlli falliti prima dell'allerta</label><select id="alert-threshold" class="select-input"><option value="1" ${alertThreshold === 1 ? 'selected' : ''}>1 controllo fallito</option><option value="2" ${alertThreshold === 2 ? 'selected' : ''}>2 controlli consecutivi (predefinito)</option><option value="3" ${alertThreshold === 3 ? 'selected' : ''}>3 controlli consecutivi</option></select>`
      + `<label class="field-label" for="health-timeout" style="margin-top:10px">Timeout massimo per verifica (workflow critici)</label><select id="health-timeout" class="select-input"><option value="500" ${probeTimeout === 500 ? 'selected' : ''}>0,5 secondi</option><option value="1000" ${probeTimeout === 1000 ? 'selected' : ''}>1 secondo (predefinito)</option><option value="2000" ${probeTimeout === 2000 ? 'selected' : ''}>2 secondi</option><option value="3000" ${probeTimeout === 3000 ? 'selected' : ''}>3 secondi</option><option value="5000" ${probeTimeout === 5000 ? 'selected' : ''}>5 secondi</option></select>`
      + `<div class="health-controls-row"><button class="button button-quiet button-small" data-action="check-health-now">Verifica manuale servizi</button></div></article>`
      + `<article class="settings-card"><h3>Canali di avviso e allerta acustica</h3><p>Configura il comportamento visivo e acustico in caso di blocco di emergenza.</p>`
      + `<label class="field-label" for="alert-audio">Segnale acustico prioritario</label><select id="alert-audio" class="select-input"><option value="true" ${audioEnabled ? 'selected' : ''}>Attivato (avviso sonoro ripetuto)</option><option value="false" ${!audioEnabled ? 'selected' : ''}>Disattivato (silenzioso)</option></select>`
      + `<label class="field-label" for="alert-visual" style="margin-top:10px">Banner visivo prioritario</label><select id="alert-visual" class="select-input"><option value="true" ${visualEnabled ? 'selected' : ''}>Attivato (banner rosso persistente)</option><option value="false" ${!visualEnabled ? 'selected' : ''}>Disattivato</option></select>`
      + `<label class="field-label" for="alert-repeat-sec" style="margin-top:10px">Frequenza ripetizione suono</label><select id="alert-repeat-sec" class="select-input"><option value="2" ${alertRepeat === 2 ? 'selected' : ''}>Ogni 2 secondi</option><option value="4" ${alertRepeat === 4 ? 'selected' : ''}>Ogni 4 secondi (predefinito)</option><option value="6" ${alertRepeat === 6 ? 'selected' : ''}>Ogni 6 secondi</option></select>`
      + `<div class="health-controls-row"><button class="button button-quiet button-small" data-action="test-alert-sound">Testa segnale acustico</button></div></article></div>`
      + `<div class="settings-section-title">Dashboard preferences</div><div class="settings-grid"><article class="settings-card"><h3>Auto-refresh dati</h3><p>Aggiornamento periodico dei dati tabellari mentre la scheda è visibile.</p><label class="field-label" for="refresh-interval">Intervallo dati</label><select id="refresh-interval" class="select-input"><option value="0" ${refreshValue === 0 ? 'selected' : ''}>Manuale</option><option value="15" ${refreshValue === 15 ? 'selected' : ''}>15 sec</option><option value="30" ${refreshValue === 30 ? 'selected' : ''}>30 sec</option><option value="60" ${refreshValue === 60 ? 'selected' : ''}>60 sec</option></select></article><article class="settings-card"><h3>Display</h3><p>Responsive layout and reduced-motion preference.</p><div class="setting-value">Light theme · prefers-reduced-motion supported</div></article></div>`
      + `<div class="settings-section-title">Backend configuration · read only</div><div class="settings-grid"><article class="settings-card"><h3>API origin</h3><p>Same-origin loopback backend integration.</p><div class="setting-value">${esc(location.origin)}${API_ROOT}</div></article><article class="settings-card"><h3>Operator session</h3><p>Sessione locale con consenso utente. Nessuna credenziale è salvata nel browser.</p><div class="setting-value">${state.token ? 'Connected · in-memory session active' : 'Disconnected'}</div></article><article class="settings-card"><h3>System 1</h3><p>Runtime configured flag, not proof of native service availability.</p><div class="setting-value">${settings ? (settings.system1_configured ? 'Configured · unverified' : 'Unavailable') : 'Connect to query backend'}</div></article><article class="settings-card"><h3>System 2</h3><p>Explicit text generation, not an authorization component.</p><div class="setting-value">${settings ? (settings.system2_configured ? 'Configured · unverified' : 'Unavailable') : 'Connect to query backend'}</div></article><article class="settings-card"><h3>Tool availability</h3><p>Per-action policy and approval checks still apply.</p><div class="setting-value">${settings ? (settings.tool_execution_available ? 'Some registered capabilities present' : 'Unavailable') : 'Connect to query backend'}</div></article><article class="settings-card"><h3>Unsupported</h3><p>Model secrets, project configuration, permission grants, database options and approval policy.</p><div class="setting-value">Unavailable · deployment-owned</div></article></div>`;
  }

  let confirmResolve = null;
  function confirmAction({ title, eyebrow = 'OPERATOR CONFIRMATION', description, button = 'Confirm', danger = false, summary = {} }) {
    $('#confirm-title').textContent = title; $('#confirm-eyebrow').textContent = eyebrow; $('#confirm-description').textContent = description;
    const submit = $('#confirm-submit'); submit.textContent = button; submit.className = `button ${danger ? 'button-danger' : 'button-primary'}`;
    $('#confirm-summary').innerHTML = `<dl>${Object.entries(summary).map(([k,v]) => `<dt>${esc(k.replaceAll('_',' '))}</dt><dd>${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</dd>`).join('')}</dl>`;
    elements.confirmDialog.showModal(); setTimeout(() => $('#confirm-cancel').focus(), 0);
    return new Promise((resolve) => { confirmResolve = resolve; });
  }
  async function approvalDecision(id, verb) {
    const key = `${verb}:${id}`; if (busy(key)) return; setBusy(key, true);
    try {
      const data = await api.get(`/operator/approvals/${encodeURIComponent(id)}`); const a = data.approval;
      if (a.status !== 'pending' || Date.parse(a.expires_at) <= Date.now()) { notify(`Backend says approval is ${a.status}; refresh to confirm its current state.`, 'error'); await render(); return; }
      if (['invalid','expired','unavailable'].includes(data.validation.status)) { notify(`Backend validation prevents this decision: ${data.validation.message}`, 'error'); return; }
      const approved = verb === 'approve'; const confirmed = await confirmAction({ title: approved ? 'Approve this operation?' : 'Reject this request?', eyebrow: approved ? 'SENSITIVE OPERATION · CONFIRM REQUIRED' : 'OPERATOR CONFIRMATION', description: approved ? 'The backend will record approval for this exact action. It does not execute the action. Scope, expiry, fingerprint and single-use are rechecked at execution.' : 'The backend will reject this pending approval. It cannot later be approved or reused.', button: approved ? 'Confirm approval' : 'Confirm rejection', danger: !approved, summary: { tool: a.tool_id, operation_id: data.operation_id || data.parameters_preview?.operation_id || 'Not applicable', project_scope: a.project_id || 'None', workflow: a.workflow_id || 'Direct action', expires: fmtTime(a.expires_at), parameters: data.parameters_preview, validation: data.validation.status } });
      if (!confirmed) return;
      const query = state.projectId ? `?project_id=${encodeURIComponent(state.projectId)}` : '';
      const result = await api.post(`/operator/approvals/${encodeURIComponent(id)}/${verb}${query}`);
      notify(`Backend response: approval ${result.status}${result.decided_by ? ` · decided by ${result.decided_by}` : ''} · ${shortId(result.approval_id, 9)}.`, approved ? 'success' : 'info'); await render();
    } catch (error) { notify(`${verb} failed: ${errorText(error)}`, 'error'); if (error instanceof ApiError && error.status === 409) await render(); }
    finally { setBusy(key, false); }
  }
  async function workflowAction(id, action) {
    const key = `${action}:${id}`; if (busy(key)) return;
    setBusy(key, true);
    try {
      const operation = action.replace('-workflow',''); const w = state.lastData.workflow;
      const confirmed = await confirmAction({ title: `${operation[0].toUpperCase()}${operation.slice(1)} workflow?`, eyebrow: operation === 'cancel' ? 'DESTRUCTIVE WORKFLOW ACTION' : 'WORKFLOW ACTION', description: operation === 'resume' ? 'The backend validates the current status and the exact approved action before continuing. A pending approval cannot be bypassed.' : operation === 'pause' ? 'The backend checkpoints this workflow. Its pending approval stays bound to the same workflow step.' : 'Cancellation is terminal; the backend preserves recorded history.', button: `Confirm ${operation}`, danger: operation === 'cancel', summary: { workflow_id: id, current_status: w?.status || 'Will be read by backend', pending_step: w?.pending_step || 'None' } });
      if (!confirmed) return;
      const query = state.projectId ? `?project_id=${encodeURIComponent(state.projectId)}` : '';
      const result = await api.post(`/operator/workflows/${encodeURIComponent(id)}/${operation}${query}`);
      notify(`Backend response: workflow ${result.status} · version ${result.version}${result.pending_step ? ` · pending ${result.pending_step}` : ''}.`, 'success'); await render();
    } catch (error) { notify(`${action.replace('-workflow','')} failed: ${errorText(error)}`, 'error'); if (error instanceof ApiError && error.status === 409) await render(); }
    finally { setBusy(key, false); }
  }

  
  async function renderMemory() {
    if (!state.token) {
      elements.view.innerHTML = heading('MEMORIA GLOBALE', 'Gestione Memoria Personale', 'Autenticazione necessaria') + noSession('gestire i ricordi persistenti');
      return;
    }

    elements.view.innerHTML = heading('MEMORIA GLOBALE', 'Gestione Memoria', 'Visualizza, modifica o elimina le informazioni apprese dalla Chat AI.') + `
      <div class="card" style="padding: 24px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px;">
          <h3 style="margin: 0; font: 700 16px var(--display); color: var(--ink);">Ricordi Personali e Preferenze</h3>
          <div class="filter-row">
             <button id="add-memory-btn" class="button button-primary">Aggiungi Ricordo</button>
             <button id="clear-memory-btn" class="button button-danger" style="background: rgba(248, 113, 113, 0.15); color: var(--red); border: 1px solid var(--red-soft);">Svuota Memoria</button>
          </div>
        </div>
        <div id="memory-list" style="display: grid; gap: 12px;">
           <div class="empty-state"><em>Caricamento memoria in corso...</em></div>
        </div>
      </div>
    `;

    const loadMemories = async () => {
      try {
        const list = await api.get('/memory/global');
        const container = document.getElementById('memory-list');
        if (!list || list.length === 0) {
          container.innerHTML = `<div class="empty-state">
            <span class="empty-icon" aria-hidden="true">🧠</span>
            <div><strong>Nessun ricordo salvato</strong><p>La Chat AI non ha ancora estratto o memorizzato alcuna informazione persistente.</p></div>
          </div>`;
          return;
        }
        
        container.innerHTML = list.map(m => `
          <div class="detail-item" style="display: flex; justify-content: space-between; align-items: flex-start; padding: 16px;">
            <div>
              <dt>${esc(m.key)} <span class="pill-latency" style="margin-left: 8px;">${esc(fmtTime(m.updated_at))}</span></dt>
              <dd style="font-size: 11px; margin-top: 8px;">${esc(typeof m.value === 'string' ? m.value : JSON.stringify(m.value))}</dd>
            </div>
            <div style="display: flex; gap: 8px;">
               <button class="button button-quiet button-small delete-mem-btn" data-key="${esc(m.key)}">Elimina</button>
            </div>
          </div>
        `).join('');
        
        container.querySelectorAll('.delete-mem-btn').forEach(btn => {
          btn.addEventListener('click', async (e) => {
             const key = e.target.dataset.key;
             if (confirm(`Eliminare definitivamente il ricordo "${key}"?`)) {
                await api.del(`/memory/global/${encodeURIComponent(key)}`);
                await loadMemories();
             }
          });
        });
      } catch (err) {
        document.getElementById('memory-list').innerHTML = `<div class="error-state">Errore nel caricamento della memoria: ${esc(err.message)}</div>`;
      }
    };
    
    await loadMemories();
    
    document.getElementById('clear-memory-btn').addEventListener('click', async () => {
      if (confirm('Sei sicuro di voler eliminare TUTTA la memoria personale? L\'azione è irreversibile.')) {
         try {
           const list = await api.get('/memory/global');
           for (const m of list) {
             await api.del(`/memory/global/${encodeURIComponent(m.key)}`);
           }
           await loadMemories();
         } catch(e) {
           alert("Errore durante lo svuotamento: " + e.message);
         }
      }
    });
    
    document.getElementById('add-memory-btn').addEventListener('click', async () => {
      const key = prompt("Inserisci la chiave (es. 'preferenza_risposta', 'nome_utente'):");
      if (!key) return;
      const val = prompt("Inserisci il valore da ricordare:");
      if (!val) return;
      
      try {
        await api.post('/memory/global', { key: key, value: val, scope: 'persistent' });
        await loadMemories();
      } catch (e) {
        alert("Errore nel salvataggio: " + e.message);
      }
    });
  }

  async function renderChat() {
    if (!state.token) {
      elements.view.innerHTML = heading('HYBRID AI FRAMEWORK', 'JEV + Nemotron Chat', 'Autenticazione necessaria') + noSession('utilizzare la chat AI');
      return;
    }
    
    // We fetch sessions
    let sessions = [];
    try {
       sessions = await api.get('/chat/sessions');
    } catch(e) {
       console.error("No chat sessions endpoint yet", e);
    }
    
    let activeSessionId = null;
    
    elements.view.innerHTML = heading('HYBRID AI FRAMEWORK', 'JEV + Nemotron Chat', 'Conversa con il sistema ibrido selezionando la modalità di esecuzione.') + `
      <div class="card" style="display: grid; grid-template-columns: 280px minmax(0, 1fr); border: none; overflow: hidden; height: 75vh;">
        
        <div class="chat-sidebar" style="border-right: 1px solid var(--line); background: rgba(0,0,0,0.2); display: flex; flex-direction: column;">
           <div style="padding: 16px; border-bottom: 1px solid var(--line);">
              <button id="new-chat-btn" class="button button-primary" style="width: 100%;">+ Nuova Chat</button>
           </div>
           <div id="session-list" style="flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px;">
              ${sessions.map(s => `
                 <div class="session-item" data-id="${esc(s.session_id)}" style="padding: 10px; border-radius: 6px; cursor: pointer; border: 1px solid transparent;">
                    <strong style="display: block; font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--ink);">${esc(s.title)}</strong>
                    <small style="color: var(--muted); font-size: 9px;">${esc(fmtTime(s.updated_at))}</small>
                 </div>
              `).join('')}
           </div>
        </div>

        <div class="chat-container" style="display: flex; flex-direction: column; background: var(--paper-alt);">
          <div class="chat-header" style="padding: 16px; border-bottom: 1px solid var(--line); display: flex; gap: 16px; align-items: center; background: var(--canvas);">
            <label class="field-label" style="margin: 0;">Modalità:</label>
            <div class="filter-row" id="chat-mode-group">
              <button class="filter-chip active" data-mode="LOW" title="LOW (Solo Nemotron)">LOW</button>
              <button class="filter-chip" data-mode="MEDIUM" title="MEDIUM (JEV + Nemotron)">MEDIUM</button>
              <button class="filter-chip" data-mode="HARD" title="HARD (Ragionamento JEV profondo)">HARD</button>
            </div>
            
            <label style="margin-left: auto; display: flex; align-items: center; gap: 8px; cursor: pointer; color: var(--muted); font-size: 10px;">
               <input type="checkbox" id="auto-memory-toggle" checked style="accent-color: var(--accent);"> Memoria Auto
            </label>
          </div>
          <div id="chat-messages" class="chat-messages" style="flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 16px;">
            <div class="empty-state" style="margin: auto; border: none; background: transparent;">
               Seleziona o avvia una nuova conversazione per iniziare.
            </div>
          </div>
          <div class="chat-input-area" style="padding: 16px; border-top: 1px solid var(--line); display: flex; gap: 8px; background: var(--canvas);">
            <textarea id="chat-input" class="text-input" placeholder="Scrivi un messaggio... (Shift+Enter per andare a capo)" style="flex: 1; min-height: 44px; resize: vertical;" disabled></textarea>
            <button id="chat-send" class="button button-primary" disabled>Invia</button>
          </div>
        </div>
      </div>
    `;

    const input = $('#chat-input');
    const sendBtn = $('#chat-send');
    const messages = $('#chat-messages');
    
    let currentMode = 'LOW';
    const modeButtons = document.querySelectorAll('#chat-mode-group .filter-chip');
    modeButtons.forEach(btn => {
      btn.addEventListener('click', (e) => {
        modeButtons.forEach(b => b.classList.remove('active'));
        e.target.classList.add('active');
        currentMode = e.target.dataset.mode;
      }, { signal: state.listeners.signal });
    });

    const appendMessage = (role, text, meta) => {
      const div = document.createElement('div');
      div.className = `chat-message ${role}`;
      div.style.padding = '14px';
      div.style.borderRadius = '10px';
      div.style.backgroundColor = role === 'user' ? 'rgba(139, 128, 255, 0.1)' : 'rgba(0,0,0,0.2)';
      div.style.alignSelf = role === 'user' ? 'flex-end' : 'flex-start';
      div.style.maxWidth = '85%';
      div.style.border = '1px solid ' + (role === 'user' ? 'var(--accent-soft)' : 'var(--line)');
      
      let html = `<strong style="color: ${role==='user'?'var(--accent)':'var(--ink)'};">${role === 'user' ? 'Tu' : 'AI'}</strong><div style="margin-top: 6px; line-height: 1.5; overflow-wrap: anywhere;">${esc(text).replace(/\n/g, '<br>')}</div>`;
      if (meta && Object.keys(meta).length > 0) {
        html += `<div style="margin-top: 10px; font-size: 0.85em; color: var(--muted); border-top: 1px solid var(--line); padding-top: 6px; display: flex; gap: 12px; flex-wrap: wrap;">`;
        if (meta.executed_mode) html += `<span>Modalità: <strong>${meta.executed_mode}</strong></span>`;
        if (meta.providers_used && meta.providers_used.length) html += `<span>Provider: ${meta.providers_used.join(' + ')}</span>`;
        if (meta.extracted_memories) html += `<span style="color: var(--green);">🧠 Estratti ${meta.extracted_memories} ricordi</span>`;
        if (meta.retrieved_memories) html += `<span style="color: var(--amber);">💡 Usati ${meta.retrieved_memories} ricordi a lungo termine</span>`;
        if (meta.error_message) html += `<span style="color: var(--red);">⚠️ Errore: ${esc(meta.error_message)}</span>`;
        html += `</div>`;
      }
      div.innerHTML = html;
      messages.appendChild(div);
      messages.scrollTop = messages.scrollHeight;
    };

    const loadSession = async (id) => {
       activeSessionId = id;
       input.disabled = false;
       sendBtn.disabled = false;
       document.querySelectorAll('.session-item').forEach(el => {
          el.style.backgroundColor = el.dataset.id === id ? 'var(--accent-soft)' : 'transparent';
          el.style.borderColor = el.dataset.id === id ? 'var(--accent)' : 'transparent';
       });
       
       messages.innerHTML = '<div class="empty-state" style="margin: auto; border: none; background: transparent;">Caricamento messaggi...</div>';
       try {
          const res = await api.get(`/chat/sessions/${id}/messages`);
          messages.innerHTML = '';
          if (!res || res.length === 0) {
             messages.innerHTML = '<div class="empty-state" style="margin: auto; border: none; background: transparent;">Nessun messaggio in questa conversazione.</div>';
          } else {
             res.forEach(msg => appendMessage(msg.role, msg.content, msg.metadata_json ? JSON.parse(msg.metadata_json) : {}));
          }
       } catch (err) {
          messages.innerHTML = `<div class="error-state">Errore nel caricamento della conversazione: ${esc(err.message)}</div>`;
       }
    };

    document.querySelectorAll('.session-item').forEach(el => {
       el.addEventListener('click', () => loadSession(el.dataset.id));
    });

    document.getElementById('new-chat-btn').addEventListener('click', async () => {
       try {
          const res = await api.post('/chat/sessions', { title: "Nuova Conversazione" });
          renderChat(); // Reload the whole view to show new session in sidebar
       } catch (err) {
          alert("Errore creazione sessione: " + err.message);
       }
    });

    const sendMessage = async () => {
      const text = input.value.trim();
      if (!text || !activeSessionId) return;
      const mode = currentMode;
      const autoMemory = document.getElementById('auto-memory-toggle').checked;
      
      input.value = '';
      input.disabled = true;
      sendBtn.disabled = true;
      
      appendMessage('user', text);
      
      const loadingId = 'loading-' + Date.now();
      const loadingDiv = document.createElement('div');
      loadingDiv.id = loadingId;
      loadingDiv.innerHTML = '<em>Generazione in corso...</em>';
      loadingDiv.style.alignSelf = 'flex-start';
      loadingDiv.style.color = 'var(--muted)';
      messages.appendChild(loadingDiv);
      messages.scrollTop = messages.scrollHeight;

      try {
        const res = await api.post(`/chat/message`, { session_id: activeSessionId, message: text, mode: mode, auto_memory: autoMemory });
        document.getElementById(loadingId).remove();
        appendMessage('assistant', res.text, res);
      } catch (err) {
        document.getElementById(loadingId).remove();
        appendMessage('assistant', 'Errore di connessione al backend.', { error_message: err.message });
      } finally {
        input.disabled = false;
        sendBtn.disabled = false;
        input.focus();
      }
    };

    sendBtn.addEventListener('click', sendMessage, { signal: state.listeners.signal });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    }, { signal: state.listeners.signal });
  }

function bindPage() {
    document.querySelectorAll('[data-action]').forEach((node) => { if (node.dataset.bound) return; node.dataset.bound = '1'; node.addEventListener('click', actionHandler, { signal: state.listeners.signal }); });
    const query = $('#execution-query'); const status = $('#execution-status'); const since = $('#execution-since'); const until = $('#execution-until');
    const apply = () => { state.executionQuery = query?.value.slice(0,128) || ''; state.executionStatus = status?.value || ''; state.executionSince = since?.value || ''; state.executionUntil = until?.value || ''; state.executionOffset = 0; state.listeners.abort(); state.listeners = new AbortController(); render(); };
    query?.addEventListener('keydown', (e) => { if (e.key === 'Enter') apply(); }, { signal: state.listeners.signal }); status?.addEventListener('change', apply, { signal: state.listeners.signal }); since?.addEventListener('change', apply, { signal: state.listeners.signal }); until?.addEventListener('change', apply, { signal: state.listeners.signal });
    $('#refresh-interval')?.addEventListener('change', (e) => { try { localStorage.setItem('laya-dashboard-refresh', String(Number(e.target.value))); } catch { notify('Storage unavailable; this preference will not persist.', 'info'); } setupRefresh(); }, { signal: state.listeners.signal });
    $('#health-interval')?.addEventListener('change', (e) => {
      const val = Number(e.target.value);
      try { localStorage.setItem('laya-health-interval', String(val)); } catch {}
      setupHealthPolling();
      if (val > 0) notify(`Polling stato servizi impostato a ${val} secondi.`, 'info');
      else notify('Polling automatico dello stato dei servizi sospeso.', 'info');
    }, { signal: state.listeners.signal });
    $('#critical-interval')?.addEventListener('change', (e) => {
      const val = Number(e.target.value);
      try { localStorage.setItem('laya-alert-critical-interval', String(val)); } catch {}
      setupHealthPolling();
    }, { signal: state.listeners.signal });
    $('#alert-threshold')?.addEventListener('change', (e) => {
      try { localStorage.setItem('laya-alert-threshold', String(e.target.value)); } catch {}
    }, { signal: state.listeners.signal });
    $('#health-timeout')?.addEventListener('change', (e) => {
      const val = Number(e.target.value);
      if (!Number.isFinite(val) || val <= 0) return;
      try { localStorage.setItem('laya-alert-timeout-ms', String(val)); } catch { notify('Storage unavailable; this preference will not persist.', 'info'); }
      notify(`Timeout di verifica System 1 impostato a ${val} ms.`, 'info');
    }, { signal: state.listeners.signal });
    $('#alert-audio')?.addEventListener('change', (e) => {
      try { localStorage.setItem('laya-alert-audio', String(e.target.value)); } catch {}
    }, { signal: state.listeners.signal });
    $('#alert-visual')?.addEventListener('change', (e) => {
      try { localStorage.setItem('laya-alert-visual', String(e.target.value)); } catch {}
    }, { signal: state.listeners.signal });
    $('#alert-repeat-sec')?.addEventListener('change', (e) => {
      try { localStorage.setItem('laya-alert-repeat-sec', String(e.target.value)); } catch {}
    }, { signal: state.listeners.signal });
  }

  async function actionHandler(event) {
    const button = event.currentTarget; const action = button.dataset.action;
    if (action === 'connect') { await performAutomaticConnection(); return; }
    if (action === 'refresh') { render(); return; }
    if (action === 'check-health-now') { await checkServicesHealth(); notify('Verifica stato servizi completata.', 'info'); return; }
    if (action === 'test-alert-sound') { playAlertBeep(); notify('Test segnale acustico emesso.', 'info'); return; }
    if (action === 'goto-approvals') { location.hash = '#approvals'; return; }
    if (action === 'view-approval') { location.hash = `#approvals?id=${encodeURIComponent(button.dataset.approval)}`; return; }
    if (action === 'approval-filter') { state.approvalFilter = button.dataset.filter; render(); return; }
    if (action === 'workflow-filter') { state.workflowFilter = button.dataset.filter; render(); return; }
    if (action === 'execution-prev') { state.executionOffset = Math.max(0, state.executionOffset - PAGE_SIZE); render(); return; }
    if (action === 'execution-next') { state.executionOffset += PAGE_SIZE; render(); return; }
    if (action === 'clear-execution') { state.executionQuery = ''; state.executionStatus = ''; state.executionSince = ''; state.executionUntil = ''; state.executionOffset = 0; render(); return; }
    if (action === 'disconnect') { await disconnectOperatorSession(); return; }
    if (action === 'approve' || action === 'reject') { await approvalDecision(button.dataset.approval, action); return; }
    if (['pause-workflow','resume-workflow','cancel-workflow'].includes(action)) await workflowAction(button.dataset.workflow, action);
  }

  function setupRefresh() {
    if (state.refreshTimer) clearInterval(state.refreshTimer); state.refreshTimer = null;
    let interval = 0; try { interval = Number(localStorage.getItem('laya-dashboard-refresh')) || 0; } catch { /* preference unavailable */ }
    if ([15,30,60].includes(interval)) state.refreshTimer = setInterval(() => { if (!document.hidden && state.token && ['overview','approvals','workflows','status'].includes(state.page)) render(); }, interval * 1000);
  }

  function bindGlobal() {
    if (elements.consentConnect) {
      elements.consentConnect.addEventListener('click', () => performAutomaticConnection());
    }
    if (elements.consentExit) {
      elements.consentExit.addEventListener('click', () => showExitScreen());
    }
    if (elements.consentRetryExit) {
      elements.consentRetryExit.addEventListener('click', () => showWelcomeScreen());
    }
    if (elements.disconnectButton) {
      elements.disconnectButton.addEventListener('click', () => disconnectOperatorSession());
    }

    // Gestori pulsanti banner allerta prioritaria
    if (elements.alertViewWorkflow) {
      elements.alertViewWorkflow.addEventListener('click', () => {
        if (state.criticalWorkflow) {
          location.hash = `#workflow/${encodeURIComponent(state.criticalWorkflow.workflow_id)}`;
        }
      });
    }
    if (elements.alertAckBtn) {
      elements.alertAckBtn.addEventListener('click', async () => {
        state.alertAcknowledged = true;
        stopAlertSoundLoop();
        notify('Ricezione allerta confermata dall\'operatore. Audio silenziato.', 'info');
        if (state.criticalWorkflow) {
          try {
            await api.post('/operator/critical-alert/event', {
              event_type: 'operator_acknowledged',
              workflow_id: state.criticalWorkflow.workflow_id,
              execution_id: state.criticalWorkflow.steps?.find((s) => s.execution_id)?.execution_id || null,
              project_id: state.criticalWorkflow.project_id || null,
              status: 'acknowledged',
              details: { operator: 'local-operator', audio_silenced: true },
            });
          } catch {}
        }
      });
    }
    if (elements.alertCheckSystem1) {
      elements.alertCheckSystem1.addEventListener('click', async () => {
        await checkServicesHealth();
        notify(`Stato System 1: ${state.servicesHealth.system1.status} (${state.servicesHealth.system1.message})`, state.servicesHealth.system1.status === 'online' ? 'success' : 'error');
      });
    }
    if (elements.alertResumeWorkflow) {
      elements.alertResumeWorkflow.addEventListener('click', async () => {
        if (!state.criticalWorkflow) return;
        if (state.servicesHealth.system1.status !== 'online') {
          notify('Impossibile riprendere: System 1 non è ancora confermato online.', 'error');
          return;
        }
        const confirmed = await confirmAction({
          title: 'Riprendere il workflow interrotto?',
          eyebrow: 'RIPRESA SICURA DOPO RIPRISTINO SYSTEM 1',
          description: 'System 1 è confermato online. La ripresa verificherà lo stato del workflow e richiederà le autorizzazioni necessarie prima di eseguire ulteriori passi.',
          button: 'Conferma ripresa controllata',
          danger: false,
          summary: {
            workflow_id: state.criticalWorkflow.workflow_id,
            project_id: state.criticalWorkflow.project_id || 'unscoped',
            system1_status: state.servicesHealth.system1.status,
            latency_ms: state.servicesHealth.system1.latency_ms,
          },
        });
        if (!confirmed) return;

        try {
          const query = state.criticalWorkflow.project_id ? `?project_id=${encodeURIComponent(state.criticalWorkflow.project_id)}` : '';
          const res = await api.post(`/operator/workflows/${encodeURIComponent(state.criticalWorkflow.workflow_id)}/resume${query}`);
          hidePriorityAlert();
          notify(`Workflow ripreso con successo (stato: ${res.status}).`, 'success');
          await api.post('/operator/critical-alert/event', {
            event_type: 'operator_resumed_workflow',
            workflow_id: state.criticalWorkflow.workflow_id,
            status: 'resumed',
            details: { new_status: res.status, system1_latency: state.servicesHealth.system1.latency_ms },
          });
          state.criticalWorkflow = null;
          await render();
        } catch (err) {
          notify('Errore durante la ripresa: ' + formatErrorMessage(err), 'error');
        }
      });
    }

    if (elements.confirmForm) {
      elements.confirmForm.addEventListener('submit', (event) => { event.preventDefault(); elements.confirmDialog.close('confirm'); });
    }
    if ($('#confirm-cancel')) {
      $('#confirm-cancel').addEventListener('click', () => elements.confirmDialog.close('cancel'));
    }
    if (elements.projectSelector) {
      elements.projectSelector.addEventListener('change', () => {
        const selected = elements.projectSelector.value;
        if (selected && !state.projects.includes(selected)) {
          state.projectId = '';
          syncProjectSelector();
          notify('That project is not in the backend-authorized project list.', 'error');
          return;
        }
        state.projectId = selected;
        state.executionOffset = 0;
        render();
      });
    }
    if (elements.confirmDialog) {
      elements.confirmDialog.addEventListener('close', () => { const resolve = confirmResolve; confirmResolve = null; resolve?.(elements.confirmDialog.returnValue === 'confirm'); });
      elements.confirmDialog.addEventListener('click', (event) => { if (event.target === elements.confirmDialog) elements.confirmDialog.close('cancel'); });
    }
    if ($('#mobile-menu')) {
      $('#mobile-menu').addEventListener('click', () => {
        const open = elements.sidebar.classList.toggle('open');
        $('#mobile-menu').setAttribute('aria-expanded', String(open));
        $('#mobile-menu').setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
      });
    }

    // Gestione visibilità scheda: sospende e riprende polling automaticamente
    if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
      document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
          if (state.healthTimer) {
            clearInterval(state.healthTimer);
            state.healthTimer = null;
          }
        } else {
          if (state.consented) {
            setupHealthPolling();
            checkServicesHealth();
          }
        }
      });
    }

    window.addEventListener('hashchange', render);
    window.addEventListener('pagehide', () => {
      state.listeners.abort();
      state.token = '';
      state.projects = [];
      state.projectId = '';
      hidePriorityAlert();
      if (state.refreshTimer) clearInterval(state.refreshTimer);
      if (state.healthTimer) clearInterval(state.healthTimer);
    });
    const clock = () => {
      const footerClock = $('#footer-clock');
      if (footerClock) footerClock.textContent = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date());
    };
    clock();
    setInterval(clock, 60_000);
  }

  bindGlobal();
  setupRefresh();
  render();
})();
