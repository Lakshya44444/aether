// Aether Dashboard App Logic

// --- Runtime configuration ---------------------------------------------------
// Nothing about where the API lives is baked into this file.
//
// Served by the gateway (production), the console is same-origin and needs no base
// at all. Under `npm run dev` the page is on :3000 while the API is on :8000, and a
// bare "/api/stats" resolves against the dev server, which does not implement it --
// that is where the 404s came from. Next.js cannot help here: it does not process
// files in public/, so NEXT_PUBLIC_* never reaches this script.
//
// Resolution order, first match wins:
//   1. #api=   hash fragment          — survives .html-stripping redirects
//   2. ?api=   query parameter        — convenient where nothing rewrites the URL
//   3. window.AETHER_API_BASE         — set by any script that loads before this one
//   4. <meta name="aether-api-base">  — set at deploy time
//   5. same origin                    — production default
//
// Both URL forms exist because static hosts differ. `serve` 301-redirects
// /console.html to /console and drops the query string with it, so a query-only
// override silently resolves to same-origin and you are back to the 404s. Browsers
// carry the fragment across a redirect, so #api= survives what ?api= does not.
function trimSlash(value) {
    return value.replace(/\/+$/, '');
}

function resolveApiBase() {
    const fromHash = new URLSearchParams(location.hash.replace(/^#/, '')).get('api');
    if (fromHash !== null) return trimSlash(fromHash);

    const fromQuery = new URLSearchParams(location.search).get('api');
    if (fromQuery !== null) return trimSlash(fromQuery);

    if (typeof window.AETHER_API_BASE === 'string') return trimSlash(window.AETHER_API_BASE);

    const meta = document.querySelector('meta[name="aether-api-base"]');
    if (meta && meta.content) return trimSlash(meta.content);

    return '';
}

const API_BASE = resolveApiBase();

/** Builds an API URL. Every request in this file goes through here. */
function api(path) {
    return `${API_BASE}${path}`;
}

// --- API key -----------------------------------------------------------------
// The gateway requires X-API-Key on /api whenever AETHER_API_KEYS is set, because
// /api/traces returns decision records for every session it has seen.
//
// This is a static page, so there is nowhere to hide a secret: the key is held in
// this browser's localStorage and the operator pastes it in once. That is a key
// scoped to a person at a console, not a key embedded in a distributed artefact --
// which is why it is prompted for rather than baked into a meta tag.
//
// Resolution: #key= / ?key= (one-time, then stored) → localStorage → none.
const KEY_STORAGE = 'aether.apiKey';

function resolveApiKey() {
    const hash = new URLSearchParams(location.hash.replace(/^#/, '')).get('key');
    const query = new URLSearchParams(location.search).get('key');
    const fromUrl = hash ?? query;
    if (fromUrl !== null) {
        try { localStorage.setItem(KEY_STORAGE, fromUrl); } catch { /* private mode */ }
        return fromUrl;
    }
    try { return localStorage.getItem(KEY_STORAGE) || ''; } catch { return ''; }
}

let API_KEY = resolveApiKey();

/** Every API request goes through here, so the key is attached in exactly one place. */
function apiFetch(path, init = {}) {
    const headers = { ...(init.headers || {}) };
    if (API_KEY) headers['X-API-Key'] = API_KEY;
    return fetch(api(path), { ...init, headers });
}

/** Asks for a key and retries. Called when the gateway answers 401. */
function promptForApiKey() {
    const entered = window.prompt(
        'This gateway requires an API key (X-API-Key).\n\n' +
        'It is stored in this browser only.'
    );
    if (entered === null) return false;
    API_KEY = entered.trim();
    try { localStorage.setItem(KEY_STORAGE, API_KEY); } catch { /* private mode */ }
    return true;
}

function numberParam(name, fallback) {
    const params = new URLSearchParams(location.search);
    const hash = new URLSearchParams(location.hash.replace(/^#/, ''));
    const raw = hash.get(name) ?? params.get(name);
    const parsed = raw === null ? NaN : Number(raw);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

// How often the feed refreshes, and how long to wait before re-reading after a write.
const POLL_INTERVAL_MS = numberParam('poll', 5000);
const REFRESH_DELAY_MS = numberParam('refresh', 500);

// --- Constants & Demo Data ---
const DECISIONS = {
    ALLOW: { label: 'ALLOW', class: 'dec-allow' },
    WARN: { label: 'WARN', class: 'dec-warn' },
    REDACT: { label: 'REDACT', class: 'dec-redact' },
    ESCALATE: { label: 'ESCALATE', class: 'dec-escalate' },
    BLOCK: { label: 'BLOCK', class: 'dec-block' }
};

// Canvas cannot read a CSS custom property directly, but it can be handed the
// computed value of one. Resolving them here means styles.css is the single
// definition of the palette and this file cannot drift out of step with it.
function cssVar(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
}

const CHART_COLORS = {
    get ALLOW()      { return cssVar('--dec-allow', '#0B7A52'); },
    get WARN()       { return cssVar('--dec-warn', '#A8690C'); },
    get REDACT()     { return cssVar('--dec-redact', '#1F5C8C'); },
    get ESCALATE()   { return cssVar('--dec-escalate', '#C2410C'); },
    get BLOCK()      { return cssVar('--dec-block', '#C8102E'); },
    get FACTUALITY() { return cssVar('--text-main', '#1A140F'); },
    get PRIVACY()    { return cssVar('--dec-redact', '#1F5C8C'); },
    get BIAS()       { return cssVar('--dec-escalate', '#C2410C'); },
    get COST()       { return cssVar('--text-muted', '#8A7D6D'); }
};



// --- State ---
let state = {
    stats: null,
    traces: [],
    selectedTrace: null,
    charts: { decision: null, risk: null },
    pollingInterval: null,
    // Which traces already carry a verdict. Seeded from /api/stats on every poll, so it
    // survives a reload and is the same for every operator; a locally-added id just
    // keeps the item from flashing back before the next poll confirms it.
    reviewed: new Set()
};

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    initCharts();
    setupEventListeners();
    fetchData(); // initial fetch
    state.pollingInterval = setInterval(fetchData, POLL_INTERVAL_MS);
});

// --- API Communication ---

// There is no demo data. This console used to fall back to a fabricated dataset --
// 145,230 evaluations, 580 false positives, ten random trace ids -- and, because the
// fetch failures were swallowed rather than raised, it displayed all of it beside a
// green "Connected" light. Invented operational numbers on a governance dashboard are
// worse than no numbers, and this is the one product where that should be obvious.
//
// When the API cannot be reached the console says so and shows nothing.
async function fetchData() {
    const [statsRes, tracesRes] = await Promise.all([
        apiFetch('/api/stats').catch(() => null),
        apiFetch('/api/traces').catch(() => null),
    ]);

    // Reachable but refusing us is a different problem from unreachable, and telling
    // an operator "offline" when the gateway is up and rejecting their key sends them
    // to debug the wrong thing.
    if (statsRes && statsRes.status === 401) {
        setConnectionStatus(false, 'Unauthorised — key refused');
        renderOffline('The gateway rejected this API key.');
        if (promptForApiKey()) fetchData();
        return;
    }

    const online = Boolean(statsRes && statsRes.ok && tracesRes && tracesRes.ok);
    setConnectionStatus(online);

    if (!online) {
        renderOffline();
        return;
    }

    try {
        updateDashboard({ stats: await statsRes.json(), traces: await tracesRes.json() });
    } catch (error) {
        console.error('Malformed response from the API', error);
        setConnectionStatus(false);
        renderOffline();
    }
}

/** Blanks every figure rather than leaving the last good one looking current. */
function renderOffline(note) {
    const message = note || 'No connection to the API.';
    const evalsEl = document.getElementById('stat-evals');
    evalsEl.dataset.value = '0';
    evalsEl.textContent = '—';
    for (const id of ['stat-latency', 'stat-fpr', 'stat-air']) {
        document.getElementById(id).textContent = '—';
    }

    document.getElementById('feed-body').innerHTML =
        `<tr><td colspan="7" class="empty-state">${message} Nothing to show.</td></tr>`;
    document.getElementById('review-queue-container').innerHTML =
        `<p class="empty-state">${message}</p>`;
    document.getElementById('session-list').innerHTML =
        `<p class="empty-state">${message}</p>`;

    if (state.charts) {
        for (const chart of Object.values(state.charts)) {
            if (chart && typeof chart.destroy === 'function') chart.destroy();
        }
        state.charts = {};
    }
}

function setConnectionStatus(connected, label) {
    const dot = document.getElementById('connection-dot');
    const text = document.getElementById('connection-text');
    if (connected) {
        dot.classList.add('active');
        dot.style.background = 'var(--dec-allow)';
        text.textContent = 'Connected';
    } else {
        dot.classList.remove('active');
        dot.style.background = 'var(--dec-block)';
        // A refused key is not an unreachable gateway, and the badge saying "offline"
        // while the panel says "rejected" sends an operator to debug the network.
        text.textContent = label || 'Offline — no data';
    }
}

// --- UI Updates ---
// Trace text is whatever some caller sent the gateway, and this console renders traces
// from every session it has seen -- not just the operator's own. Interpolating that raw
// makes one caller's output_text executable in another operator's browser.
function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

function updateDashboard(data) {
    for (const id of data.stats.reviewed_trace_ids || []) state.reviewed.add(id);
    updateStats(data.stats);
    updateCharts(data.stats);
    updateFeed(data.traces);
    updateReviewQueue(data.traces);
    updateSessions(data.traces);
}

// Number animation
function animateValue(obj, start, end, duration) {
    let startTimestamp = null;
    const step = (timestamp) => {
        if (!startTimestamp) startTimestamp = timestamp;
        const progress = Math.min((timestamp - startTimestamp) / duration, 1);
        const current = Math.floor(progress * (end - start) + start);
        // Format with commas if large
        obj.innerHTML = current > 999 ? current.toLocaleString() : current;
        if (progress < 1) {
            window.requestAnimationFrame(step);
        } else {
            obj.dataset.value = end;
        }
    };
    window.requestAnimationFrame(step);
}

function updateStats(stats) {
    const evalsEl = document.getElementById('stat-evals');
    if (parseInt(evalsEl.dataset.value) !== stats.total_evaluations) {
        animateValue(evalsEl, parseInt(evalsEl.dataset.value) || 0, stats.total_evaluations, 1000);
    }
    
    document.getElementById('stat-latency').textContent = Math.round(stats.avg_latency_ms);
    
    const fpr = ((stats.false_positive_count / stats.total_evaluations) * 100).toFixed(2);
    document.getElementById('stat-fpr').textContent = isNaN(fpr) ? 0 : fpr;
    
    // alert_to_incident_rate is a share in [0,1] and the tile appends a % sign, so the
    // raw float was both wrong by 100x and 17 digits wide.
    const air = (stats.alert_to_incident_rate || 0) * 100;
    document.getElementById('stat-air').textContent = air.toFixed(2);
}

// --- Charts ---
function initCharts() {
    Chart.defaults.color = '#8a8a99';
    Chart.defaults.font.family = "'Inter', sans-serif";

    const decCtx = document.getElementById('decisionChart').getContext('2d');
    state.charts.decision = new Chart(decCtx, {
        type: 'doughnut',
        data: {
            labels: ['ALLOW', 'WARN', 'REDACT', 'ESCALATE', 'BLOCK'],
            datasets: [{
                data: [0, 0, 0, 0, 0],
                backgroundColor: [CHART_COLORS.ALLOW, CHART_COLORS.WARN, CHART_COLORS.REDACT, CHART_COLORS.ESCALATE, CHART_COLORS.BLOCK],
                borderWidth: 0,
                hoverOffset: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'right' }
            },
            cutout: '70%'
        }
    });

    const riskCtx = document.getElementById('riskChart').getContext('2d');
    state.charts.risk = new Chart(riskCtx, {
        type: 'bar',
        data: {
            labels: ['Factuality', 'Privacy', 'Bias', 'Cost'],
            datasets: [{
                label: 'Flags',
                data: [0, 0, 0, 0],
                backgroundColor: [CHART_COLORS.FACTUALITY, CHART_COLORS.PRIVACY, CHART_COLORS.BIAS, CHART_COLORS.COST],
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: { grid: { color: 'rgba(255,255,255,0.05)' } },
                x: { grid: { display: false } }
            }
        }
    });
}

function updateCharts(stats) {
    if (!stats) return;
    
    const d = stats.decisions;
    state.charts.decision.data.datasets[0].data = [d.ALLOW, d.WARN, d.REDACT, d.ESCALATE, d.BLOCK];
    state.charts.decision.update();

    const r = stats.risk_distribution;
    state.charts.risk.data.datasets[0].data = [r.factuality, r.privacy, r.bias, r.cost];
    state.charts.risk.update();
}

// --- Feed & Rendering ---
function getRiskColor(score) {
    if (score < 0.3) return CHART_COLORS.ALLOW;
    if (score < 0.7) return CHART_COLORS.WARN;
    return CHART_COLORS.BLOCK;
}

function updateFeed(traces) {
    const tbody = document.getElementById('feed-body');
    tbody.innerHTML = '';
    
    traces.slice(0, 15).forEach((trace, idx) => {
        const tr = document.createElement('tr');
        if (idx === 0) tr.classList.add('new-entry');
        
        const time = new Date(trace.timestamp).toLocaleTimeString();
        const decInfo = DECISIONS[trace.decision] || DECISIONS.ALLOW;
        const risk = trace.risk_assessment?.current_turn_risk || 0;
        
        tr.innerHTML = `
            <td class="mono-text">${time}</td>
            <td><span class="badge badge-outline">${esc(trace.use_case)}</span></td>
            <td><span class="badge badge-outline">${esc(trace.action)}</span></td>
            <td><span class="badge ${decInfo.class}">${decInfo.label}</span></td>
            <td>
                <div style="display:flex; align-items:center; gap:8px;">
                    <span class="mono-text">${risk.toFixed(2)}</span>
                    <div class="risk-bar-container">
                        <div class="risk-bar" style="width: ${risk * 100}%; background: ${getRiskColor(risk)}"></div>
                    </div>
                </div>
            </td>
            <td class="mono-text">${Math.round(trace.total_latency_ms)}ms</td>
            <td style="text-align:right;">›</td>
        `;
        
        tr.onclick = () => openTraceModal(trace);
        tbody.appendChild(tr);
    });
}

// Folded out of the traces already fetched rather than fanning out to /api/sessions/{id}
// per session: every field shown is on the trace, and N+1 requests on a 5s poll is not.
function updateSessions(traces) {
    const container = document.getElementById('session-list');
    const sessions = new Map();

    // Traces arrive newest first, so the first row seen for a session is its latest turn.
    for (const trace of traces) {
        const seen = sessions.get(trace.session_id);
        if (seen) { seen.turns += 1; continue; }
        sessions.set(trace.session_id, { turns: 1, latest: trace });
    }

    const items = [...sessions.entries()].slice(0, 6);
    if (items.length === 0) {
        container.innerHTML = '<div class="empty-state">No sessions yet.</div>';
        return;
    }

    container.innerHTML = items.map(([id, s]) => {
        const risk = s.latest.risk_assessment || {};
        const decInfo = DECISIONS[s.latest.decision] || DECISIONS.ALLOW;
        return `
        <div class="session-item">
            <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
                <span class="mono-text" style="font-size:0.8rem;">${esc(id)}</span>
                <span class="badge ${decInfo.class}">${decInfo.label}</span>
            </div>
            <div style="font-size:0.85rem; color:var(--text-muted); margin-top:6px;">
                ${s.turns} turn${s.turns === 1 ? '' : 's'}
                &middot; exposure ${(risk.session_exposure ?? 0).toFixed(2)}
                &middot; ${esc(risk.trajectory || 'stable')}
            </div>
        </div>`;
    }).join('');
}

function updateReviewQueue(traces) {
    const container = document.getElementById('review-queue-container');
    container.innerHTML = '';
    
    const toReview = traces
        .filter(t => t.decision === 'ESCALATE' && !state.reviewed.has(t.trace_id))
        .slice(0, 5);
    
    if(toReview.length === 0) {
        container.innerHTML = '<div style="color:var(--text-muted); text-align:center; padding:20px;">No items pending review.</div>';
        return;
    }

    toReview.forEach(trace => {
        const div = document.createElement('div');
        div.className = 'review-item';
        div.innerHTML = `
            <div style="display:flex; justify-content:space-between;">
                <span class="mono-text">${esc(trace.trace_id)}</span>
                <span class="badge badge-outline">${esc(trace.use_case)}</span>
            </div>
            <div style="font-size:0.85rem; color:var(--text-muted);">
                Action: ${esc(trace.action)} | Risk: ${(trace.risk_assessment?.current_turn_risk||0).toFixed(2)}
            </div>
            <div class="review-actions">
                <button class="btn btn-approve" onclick="handleReview('${esc(trace.trace_id)}', true)">Approve</button>
                <button class="btn btn-reject" onclick="handleReview('${esc(trace.trace_id)}', false)">Reject</button>
            </div>
        `;
        container.appendChild(div);
    });
}

/** What the caller actually receives, which is the question the panel exists to answer.

    `corrected_output` is null whenever the pipeline did not rewrite the text -- which is
    most decisions -- so keying the display off it alone showed nothing at all for ALLOW,
    WARN and BLOCK. Null means "unchanged", not "nothing", and only BLOCK releases
    nothing. The submitted text is right here in the form, so no API round trip is needed
    to show the unchanged case.
*/
function releasedOutput(data, submitted) {
    switch (data.decision) {
        case 'BLOCK':
            return { label: 'Released to the caller', text: null,
                     note: 'Nothing. The action does not happen.' };
        case 'ESCALATE':
            return { label: 'Held for a human', text: data.corrected_output || submitted,
                     note: 'Not released until a reviewer rules on it.' };
        case 'REDACT':
            return { label: 'Released, masked', text: data.corrected_output || submitted };
        default:
            return { label: 'Released unchanged', text: data.corrected_output || submitted };
    }
}

function renderTestResult(data, submitted) {
    const result = document.getElementById('test-result');
    const decInfo = DECISIONS[data.decision] || DECISIONS.ALLOW;
    const scores = (data.trace?.detection_results || [])
        .map((d) => `${esc(d.category)} ${d.score.toFixed(2)}`)
        .join('  &middot;  ');

    const out = releasedOutput(data, submitted);
    const changed = out.text !== null && out.text !== submitted;

    result.innerHTML = `
        <div style="display:flex; gap:12px; align-items:center; margin-bottom:8px;">
            <span class="badge ${decInfo.class}">${decInfo.label}</span>
            <span class="mono-text" style="font-size:0.75rem; color:var(--text-muted);">${esc(data.trace?.trace_id)}</span>
        </div>
        <div style="font-size:0.85rem; margin-bottom:10px;">${esc(data.reason)}</div>

        <div style="margin-bottom:10px;">
            <div style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.08em; color:var(--text-muted); margin-bottom:4px;">
                ${esc(out.label)}${changed ? ' &middot; changed' : ''}
            </div>
            <div class="mono-text" style="font-size:0.85rem; background:var(--bg-base); border:1px solid var(--border-glass); padding:10px;">
                ${out.text === null
                    ? `<span style="color:var(--text-muted);">${esc(out.note)}</span>`
                    : esc(out.text)}
            </div>
            ${out.note && out.text !== null
                ? `<div style="font-size:0.75rem; color:var(--text-muted); margin-top:4px;">${esc(out.note)}</div>`
                : ''}
        </div>

        <div class="mono-text" style="font-size:0.8rem; color:var(--text-muted);">${scores}</div>
    `;
    result.classList.remove('hidden');
    // The panel is the last thing on a long page, so on a short viewport the verdict
    // landed below the fold and the button looked like it had done nothing.
    result.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

window.handleReview = async (traceId, approved) => {
    const container = document.getElementById('review-queue-container');
    try {
        const res = await apiFetch('/api/review', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ trace_id: traceId, approved, reviewer_id: 'admin', reason: 'Dashboard review' })
        });
        // A 404 or a refused key used to sail through as a recorded verdict.
        if (!res.ok) throw new Error(`the gateway answered ${res.status}`);
        state.reviewed.add(traceId);
    } catch (error) {
        container.insertAdjacentHTML('afterbegin',
            `<p class="empty-state">Review not recorded \u2014 ${esc(error.message)}.</p>`);
        return;
    }
    fetchData(); // Refresh
};

// --- Modal ---
function openTraceModal(trace) {
    document.getElementById('modal-trace-id').textContent = trace.trace_id;
    
    const body = document.getElementById('modal-body');
    const decInfo = DECISIONS[trace.decision] || DECISIONS.ALLOW;
    
    body.innerHTML = `
        <div style="display:flex; gap:16px; margin-bottom: 20px;">
            <span class="badge ${decInfo.class}">DECISION: ${decInfo.label}</span>
            <span class="badge badge-outline">${esc(trace.use_case)}</span>
            <span class="badge badge-outline">${esc(trace.action)}</span>
        </div>
        
        <div style="display:grid; grid-template-columns: 1fr 1fr; gap:20px; margin-bottom:20px;">
            <div class="glass-panel" style="padding:16px; background:rgba(0,0,0,0.2)">
                <h3 style="margin-bottom:8px; color:var(--text-muted)">Input</h3>
                <div style="font-family:var(--font-mono); font-size:0.85rem">${esc(trace.input_text) || 'N/A'}</div>
            </div>
            <div class="glass-panel" style="padding:16px; background:rgba(0,0,0,0.2)">
                <h3 style="margin-bottom:8px; color:var(--text-muted)">Output</h3>
                <div style="font-family:var(--font-mono); font-size:0.85rem">${esc(trace.output_text) || 'N/A'}</div>
            </div>
        </div>
        
        <h3 style="margin-bottom:8px; color:var(--text-muted)">Risk Assessment</h3>
        <pre style="background:var(--bg-base); border:1px solid var(--border-glass); padding:12px; font-family:var(--font-mono); font-size:0.8rem; overflow-x:auto;">
${JSON.stringify(trace.risk_assessment, null, 2)}
        </pre>
    `;
    
    document.getElementById('trace-modal').classList.add('active');
}

function setupEventListeners() {
    document.getElementById('modal-close').addEventListener('click', () => {
        document.getElementById('trace-modal').classList.remove('active');
    });
    
    // Close on click outside
    document.getElementById('trace-modal').addEventListener('click', (e) => {
        if(e.target.id === 'trace-modal') {
            e.target.classList.remove('active');
        }
    });

    // Test form
    document.getElementById('test-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const payload = {
            use_case: document.getElementById('test-use-case').value,
            action: document.getElementById('test-action').value,
            input_text: document.getElementById('test-input').value,
            output_text: document.getElementById('test-output').value,
            // A fresh session per submission. This was the fixed id 'sess_test', so every
            // probe anyone had ever run accumulated into one session's exposure and the
            // panel eventually escalated everything -- "we are open nine to five" came
            // back ESCALATE. The panel exists to test a text against an action, not to
            // replay a conversation; multi-turn behaviour is what the demo script shows.
            session_id: `sess_test-${Date.now()}`
        };
        
        const btn = e.target.querySelector('button');
        const result = document.getElementById('test-result');
        btn.textContent = 'Evaluating...';
        btn.disabled = true;

        try {
            const res = await apiFetch('/api/evaluate', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            // fetch only rejects on a transport failure, so a 401 or a 500 arrives here
            // as a perfectly good response. Unraised, a refused key rendered as a verdict.
            if (!res.ok) throw new Error(`the gateway answered ${res.status}`);
            renderTestResult(await res.json(), payload.output_text);
            setTimeout(() => fetchData(), REFRESH_DELAY_MS); // pick up the row it just wrote
        } catch (error) {
            result.innerHTML = `<p class="empty-state">Evaluation failed \u2014 ${esc(error.message)}.</p>`;
            result.classList.remove('hidden');
        }

        btn.textContent = 'Evaluate';
        btn.disabled = false;
        // The form is deliberately not reset: the panel is for changing one field and
        // resubmitting, and a verdict you cannot see the input to is not worth much.
    });
}
