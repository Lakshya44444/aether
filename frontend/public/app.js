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
    pollingInterval: null
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
        fetch(api('/api/stats')).catch(() => null),
        fetch(api('/api/traces')).catch(() => null),
    ]);

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
function renderOffline() {
    const evalsEl = document.getElementById('stat-evals');
    evalsEl.dataset.value = '0';
    evalsEl.textContent = '—';
    for (const id of ['stat-latency', 'stat-fpr', 'stat-air']) {
        document.getElementById(id).textContent = '—';
    }

    document.getElementById('feed-body').innerHTML =
        '<tr><td colspan="7" class="empty-state">No connection to the API. ' +
        'Nothing to show.</td></tr>';
    document.getElementById('review-queue-container').innerHTML =
        '<p class="empty-state">No connection to the API.</p>';

    if (state.charts) {
        for (const chart of Object.values(state.charts)) {
            if (chart && typeof chart.destroy === 'function') chart.destroy();
        }
        state.charts = {};
    }
}

function setConnectionStatus(connected) {
    const dot = document.getElementById('connection-dot');
    const text = document.getElementById('connection-text');
    if (connected) {
        dot.classList.add('active');
        dot.style.background = 'var(--dec-allow)';
        text.textContent = 'Connected';
    } else {
        dot.classList.remove('active');
        dot.style.background = 'var(--dec-block)';
        text.textContent = 'Offline — no data';
    }
}

// --- UI Updates ---
function updateDashboard(data) {
    updateStats(data.stats);
    updateCharts(data.stats);
    updateFeed(data.traces);
    updateReviewQueue(data.traces);
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
    
    document.getElementById('stat-air').textContent = stats.alert_to_incident_rate || 0;
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
            <td><span class="badge badge-outline">${trace.use_case}</span></td>
            <td><span class="badge badge-outline">${trace.action}</span></td>
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

function updateReviewQueue(traces) {
    const container = document.getElementById('review-queue-container');
    container.innerHTML = '';
    
    const toReview = traces.filter(t => t.decision === 'ESCALATE').slice(0, 5);
    
    if(toReview.length === 0) {
        container.innerHTML = '<div style="color:var(--text-muted); text-align:center; padding:20px;">No items pending review.</div>';
        return;
    }

    toReview.forEach(trace => {
        const div = document.createElement('div');
        div.className = 'review-item';
        div.innerHTML = `
            <div style="display:flex; justify-content:space-between;">
                <span class="mono-text">${trace.trace_id}</span>
                <span class="badge badge-outline">${trace.use_case}</span>
            </div>
            <div style="font-size:0.85rem; color:var(--text-muted);">
                Action: ${trace.action} | Risk: ${(trace.risk_assessment?.current_turn_risk||0).toFixed(2)}
            </div>
            <div class="review-actions">
                <button class="btn btn-approve" onclick="handleReview('${trace.trace_id}', true)">Approve</button>
                <button class="btn btn-reject" onclick="handleReview('${trace.trace_id}', false)">Reject</button>
            </div>
        `;
        container.appendChild(div);
    });
}

window.handleReview = async (traceId, approved) => {
    try {
        await fetch(api('/api/review'), {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ trace_id: traceId, approved, reviewer_id: 'admin', reason: 'Dashboard review' })
        });
    } catch {
        console.log("Review submitted (mock)", traceId, approved);
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
            <span class="badge badge-outline">${trace.use_case}</span>
            <span class="badge badge-outline">${trace.action}</span>
        </div>
        
        <div style="display:grid; grid-template-columns: 1fr 1fr; gap:20px; margin-bottom:20px;">
            <div class="glass-panel" style="padding:16px; background:rgba(0,0,0,0.2)">
                <h3 style="margin-bottom:8px; color:var(--text-muted)">Input</h3>
                <div style="font-family:var(--font-mono); font-size:0.85rem">${trace.input_text || 'N/A'}</div>
            </div>
            <div class="glass-panel" style="padding:16px; background:rgba(0,0,0,0.2)">
                <h3 style="margin-bottom:8px; color:var(--text-muted)">Output</h3>
                <div style="font-family:var(--font-mono); font-size:0.85rem">${trace.output_text || 'N/A'}</div>
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
            session_id: 'sess_test'
        };
        
        const btn = e.target.querySelector('button');
        btn.textContent = 'Evaluating...';
        
        try {
            const res = await fetch(api('/api/evaluate'), {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            // Show result
            console.log("Eval result", data);
        } catch {
            console.log("Mock eval submitted", payload);
            setTimeout(() => fetchData(), REFRESH_DELAY_MS); // refresh feed
        }
        
        btn.textContent = 'Evaluate';
        e.target.reset();
    });
}
