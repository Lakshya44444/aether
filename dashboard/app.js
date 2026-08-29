// Sentinel Dashboard App Logic

// --- Constants & Demo Data ---
const DECISIONS = {
    ALLOW: { label: 'ALLOW', class: 'dec-allow' },
    WARN: { label: 'WARN', class: 'dec-warn' },
    REDACT: { label: 'REDACT', class: 'dec-redact' },
    ESCALATE: { label: 'ESCALATE', class: 'dec-escalate' },
    BLOCK: { label: 'BLOCK', class: 'dec-block' }
};

// The five decision colours, matching the policy states elsewhere in the product.
// Canvas cannot read a CSS custom property, so these are the one place the palette
// is repeated -- keep them in step with :root in styles.css.
const CHART_COLORS = {
    ALLOW: '#0B7A52',
    WARN: '#A8690C',
    REDACT: '#1F5C8C',
    ESCALATE: '#C2410C',
    BLOCK: '#C8102E',
    FACTUALITY: '#4A3F35',
    PRIVACY: '#1F5C8C',
    BIAS: '#C2410C',
    COST: '#8A7D6D'
};

// Generate deterministic demo data for fallback
function getDemoData() {
    return {
        stats: {
            total_evaluations: 145230,
            avg_latency_ms: 142,
            false_positive_count: 580,
            alert_to_incident_rate: 12.4,
            decisions: { ALLOW: 120000, WARN: 15000, REDACT: 5000, ESCALATE: 3230, BLOCK: 2000 },
            risk_distribution: { factuality: 8500, privacy: 4200, bias: 1200, cost: 600 }
        },
        traces: Array.from({length: 10}).map((_, i) => ({
            trace_id: 'TRC-' + Math.random().toString(36).substring(2, 10).toUpperCase(),
            timestamp: new Date(Date.now() - i * 60000).toISOString(),
            use_case: ['customer_support', 'internal_copilot', 'finance_agent'][i % 3],
            action: ['generate_text', 'draft_email', 'execute_payment'][i % 3],
            decision: Object.keys(DECISIONS)[i % 5],
            total_latency_ms: 120 + Math.floor(Math.random() * 100),
            risk_assessment: { current_turn_risk: Math.random() }
        }))
    };
}

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
    state.pollingInterval = setInterval(fetchData, 5000);
});

// --- API Communication ---
async function fetchData() {
    try {
        const statsRes = await fetch('/api/stats').catch(() => null);
        const tracesRes = await fetch('/api/traces').catch(() => null);

        let data = getDemoData(); // fallback

        if (statsRes && statsRes.ok) data.stats = await statsRes.json();
        if (tracesRes && tracesRes.ok) data.traces = await tracesRes.json();

        updateDashboard(data);
        setConnectionStatus(true);
    } catch {
        console.error("Failed to fetch data, using demo data", e);
        updateDashboard(getDemoData());
        setConnectionStatus(false);
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
        text.textContent = 'Offline (Demo Mode)';
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
        await fetch('/api/review', {
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
            const res = await fetch('/api/evaluate', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            // Show result
            console.log("Eval result", data);
        } catch {
            console.log("Mock eval submitted", payload);
            setTimeout(() => fetchData(), 500); // refresh feed
        }
        
        btn.textContent = 'Evaluate';
        e.target.reset();
    });
}
