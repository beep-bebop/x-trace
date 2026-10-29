/* === X-Trace SPA Frontend === */
const API = '/api';
let currentView = 'leaderboard';
let radarChartInstance = null;

// === Navigation ===
document.querySelectorAll('[data-view]').forEach(el => {
    el.addEventListener('click', e => {
        e.preventDefault();
        const view = el.dataset.view;
        if (view) switchView(view);
    });
});

function switchView(view) {
    currentView = view;
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    const section = document.getElementById(`view-${view}`);
    if (section) section.classList.add('active');
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const navItem = document.querySelector(`.nav-item[data-view="${view}"]`);
    if (navItem) navItem.classList.add('active');

    if (view === 'leaderboard') loadLeaderboard();
    else if (view === 'bloggers') loadBloggers();
    else if (view === 'reports') loadReports();
}

// === API helpers ===
async function api(path, options = {}) {
    const res = await fetch(`${API}${path}`, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || res.statusText);
    }
    return res.json();
}

// === Session status ===
async function checkSession() {
    try {
        const data = await api('/session');
        const el = document.getElementById('sessionStatus');
        if (data.has_session) {
            el.className = 'session-status active';
            el.querySelector('span').textContent = 'Session active';
        } else {
            el.className = 'session-status inactive';
            el.querySelector('span').textContent = 'No session (run x-trace login)';
        }
    } catch {
        document.getElementById('sessionStatus').className = 'session-status inactive';
    }
}

// === Leaderboard ===
async function loadLeaderboard() {
    const tbody = document.getElementById('leaderboardBody');
    tbody.innerHTML = '<tr><td colspan="9" class="empty-state">Loading...</td></tr>';
    try {
        const data = await api('/leaderboard');
        if (!data.length) {
            tbody.innerHTML = '<tr><td colspan="9" class="empty-state">No bloggers evaluated yet</td></tr>';
            return;
        }
        tbody.innerHTML = data.map((b, i) => {
            const hitRate = b.per_metric.find(m => m.name === 'hit_rate');
            const avgRet = b.per_metric.find(m => m.name === 'avg_return');
            const sharpe = b.per_metric.find(m => m.name === 'sharpe');
            const coverage = b.per_metric.find(m => m.name === 'coverage');
            const scoreClass = b.overall_score >= 70 ? 'high' : b.overall_score >= 50 ? 'mid' : 'low';
            const hitVal = hitRate && hitRate.raw_value !== null ? `${(hitRate.raw_value * 100).toFixed(1)}%` : '—';
            const retVal = avgRet && avgRet.raw_value !== null ? `<span class="${avgRet.raw_value >= 0 ? 'return-pos' : 'return-neg'}">${avgRet.raw_value >= 0 ? '+' : ''}${avgRet.raw_value.toFixed(2)}%</span>` : '—';
            const shaVal = sharpe && sharpe.raw_value !== null ? sharpe.raw_value.toFixed(2) : '—';
            const covVal = coverage && coverage.raw_value !== null ? Math.round(coverage.raw_value) : '—';
            return `<tr class="blogger-row" data-username="${b.username}">
                <td>${i + 1}</td>
                <td><strong>@${b.username}</strong></td>
                <td class="score-cell ${scoreClass}">${b.overall_score.toFixed(2)}</td>
                <td><span class="grade-badge grade-${b.grade}">${b.grade}</span></td>
                <td>${b.sample_size}</td>
                <td>${hitVal}</td>
                <td>${retVal}</td>
                <td>${shaVal}</td>
                <td>${covVal}</td>
            </tr>`;
        }).join('');
        document.querySelectorAll('.blogger-row').forEach(row => {
            row.addEventListener('click', () => showDetail(row.dataset.username));
        });
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="9" class="empty-state">Error: ${e.message}</td></tr>`;
    }
}

// === Bloggers grid ===
async function loadBloggers() {
    const grid = document.getElementById('bloggersGrid');
    grid.innerHTML = '<div class="empty-state">Loading...</div>';
    try {
        const data = await api('/bloggers');
        if (!data.length) {
            grid.innerHTML = '<div class="empty-state">No bloggers in database</div>';
            return;
        }
        grid.innerHTML = data.map(b => `
            <div class="blogger-card" data-username="${b.username}">
                <div class="blogger-card-header">
                    <span class="blogger-card-name">@${b.username}</span>
                </div>
                <div class="blogger-card-stats">
                    <div class="blogger-stat"><div class="blogger-stat-value">${b.tweet_count}</div><div class="blogger-stat-label">Tweets</div></div>
                    <div class="blogger-stat"><div class="blogger-stat-value">${b.prediction_count}</div><div class="blogger-stat-label">Predictions</div></div>
                    <div class="blogger-stat"><div class="blogger-stat-value">${b.result_count}</div><div class="blogger-stat-label">Results</div></div>
                </div>
            </div>
        `).join('');
        document.querySelectorAll('.blogger-card').forEach(card => {
            card.addEventListener('click', () => showDetail(card.dataset.username));
        });
    } catch (e) {
        grid.innerHTML = `<div class="empty-state">Error: ${e.message}</div>`;
    }
}

// === Detail view ===
async function showDetail(username) {
    switchView('detail');
    document.getElementById('detailUsername').textContent = `@${username}`;
    document.getElementById('detailBadges').innerHTML = '';
    document.getElementById('detailScore').textContent = '--';
    document.getElementById('detailGrade').textContent = '--';
    document.getElementById('metricsTable').innerHTML = '';
    document.getElementById('predictionsBody').innerHTML = '';
    document.getElementById('resultsBody').innerHTML = '';
    document.getElementById('tweetsList').innerHTML = '';

    try {
        const data = await api(`/bloggers/${username}/detail`);

        // Badges
        document.getElementById('detailBadges').innerHTML = `
            <span class="badge">${data.tweet_count} tweets</span>
            <span class="badge">${data.prediction_count} predictions</span>
            <span class="badge">${data.result_count} results</span>
        `;

        // Score
        if (data.score) {
            const score = data.score.overall_score;
            document.getElementById('detailScore').textContent = score.toFixed(2);
            document.getElementById('detailGrade').innerHTML = `<span class="grade-badge grade-${data.score.grade}">${data.score.grade}</span>`;

            // Metrics table
            document.getElementById('metricsTable').innerHTML = data.score.per_metric.map(m => {
                const pct = m.normalized_score.toFixed(0);
                const color = m.normalized_score >= 70 ? 'var(--success)' : m.normalized_score >= 50 ? 'var(--warning)' : 'var(--danger)';
                const raw = m.raw_value !== null ? (m.name === 'hit_rate' ? `${(m.raw_value * 100).toFixed(1)}%` : m.name === 'avg_return' ? `${m.raw_value >= 0 ? '+' : ''}${m.raw_value.toFixed(2)}%` : m.raw_value.toFixed(4)) : '—';
                return `<div class="metric-row">
                    <span class="metric-name">${m.name}</span>
                    <div class="metric-bar-wrap">
                        <div class="metric-bar"><div class="metric-bar-fill" style="width:${pct}%;background:${color}"></div></div>
                        <span class="metric-value">${m.normalized_score.toFixed(1)}</span>
                    </div>
                </div>`;
            }).join('');

            // Radar chart
            drawRadarChart(data.score.per_metric);
        }

        // Predictions
        if (data.predictions.length) {
            document.getElementById('predictionsBody').innerHTML = data.predictions.map(p => {
                const dirClass = p.direction === 'bullish' ? 'dir-bullish' : p.direction === 'bearish' ? 'dir-bearish' : 'dir-neutral';
                return `<tr>
                    <td>${p.analyzed_at ? p.analyzed_at.slice(0, 10) : '—'}</td>
                    <td><strong>${p.ticker}</strong></td>
                    <td class="${dirClass}">${p.direction}</td>
                    <td>${p.prediction_type}</td>
                    <td>${p.target_price ? '$' + p.target_price : '—'}</td>
                    <td>${p.confidence}</td>
                    <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${p.rationale}">${p.rationale || '—'}</td>
                </tr>`;
            }).join('');
        } else {
            document.getElementById('predictionsBody').innerHTML = '<tr><td colspan="7" class="empty-state">No predictions</td></tr>';
        }

        // Results
        if (data.results.length) {
            // Build a lookup of predictions for ticker info
            const predMap = {};
            data.predictions.forEach(p => { predMap[p.prediction_id] = p; });

            document.getElementById('resultsBody').innerHTML = data.results.map(r => {
                const pred = predMap[r.prediction_id];
                const ticker = pred ? pred.ticker : '—';
                const retClass = r.return_pct >= 0 ? 'return-pos' : 'return-neg';
                const dirIcon = r.direction_correct ? '✓' : '✗';
                const dirColor = r.direction_correct ? 'var(--success)' : 'var(--danger)';
                return `<tr>
                    <td>${r.window}</td>
                    <td>${r.base_date}</td>
                    <td>${r.end_date}</td>
                    <td><strong>${ticker}</strong></td>
                    <td class="${retClass}">${r.return_pct >= 0 ? '+' : ''}${r.return_pct.toFixed(2)}%</td>
                    <td style="color:${dirColor}">${dirIcon}</td>
                    <td class="return-neg">${r.max_drawdown_pct.toFixed(2)}%</td>
                    <td class="return-pos">${r.max_runup_pct.toFixed(2)}%</td>
                    <td>${r.alpha_pct !== null ? (r.alpha_pct >= 0 ? '+' : '') + r.alpha_pct.toFixed(2) + '%' : '—'}</td>
                </tr>`;
            }).join('');
        } else {
            document.getElementById('resultsBody').innerHTML = '<tr><td colspan="9" class="empty-state">No backtest results</td></tr>';
        }

        // Tweets
        if (data.tweets.length) {
            document.getElementById('tweetsList').innerHTML = data.tweets.map(t => `
                <div class="tweet-item">
                    <div class="tweet-meta"><span>${t.created_at ? t.created_at.slice(0, 16).replace('T', ' ') : ''}</span><span>ID: ${t.tweet_id}</span></div>
                    <div class="tweet-text">${t.text}</div>
                    <div class="tweet-engagement">
                        <span>❤️ ${t.like_count}</span>
                        <span>🔁 ${t.retweet_count}</span>
                        <span>💬 ${t.reply_count}</span>
                    </div>
                </div>
            `).join('');
        } else {
            document.getElementById('tweetsList').innerHTML = '<div class="empty-state">No tweets</div>';
        }
    } catch (e) {
        document.getElementById('predictionsBody').innerHTML = `<tr><td colspan="7" class="empty-state">Error: ${e.message}</td></tr>`;
    }
}

// === Radar Chart ===
function drawRadarChart(metrics) {
    const ctx = document.getElementById('radarChart').getContext('2d');
    if (radarChartInstance) radarChartInstance.destroy();

    const labels = metrics.map(m => m.name.replace(/_/g, ' '));
    const scores = metrics.map(m => m.normalized_score);

    radarChartInstance = new Chart(ctx, {
        type: 'radar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Score',
                data: scores,
                backgroundColor: 'rgba(59, 130, 246, 0.15)',
                borderColor: 'rgba(59, 130, 246, 1)',
                borderWidth: 2,
                pointBackgroundColor: 'rgba(59, 130, 246, 1)',
                pointRadius: 4,
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                r: {
                    min: 0, max: 100,
                    grid: { color: 'rgba(255,255,255,0.08)' },
                    angleLines: { color: 'rgba(255,255,255,0.08)' },
                    pointLabels: { color: '#8b9bb4', font: { size: 11 } },
                    ticks: { display: false, stepSize: 25 }
                }
            }
        }
    });
}

// === Tabs ===
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
        btn.classList.add('active');
        document.getElementById(`tab-${btn.dataset.tab}`).classList.remove('hidden');
    });
});

// === Run pipeline ===
const today = new Date().toISOString().slice(0, 10);
const threeMonthsAgo = new Date(Date.now() - 90 * 86400000).toISOString().slice(0, 10);
document.getElementById('runEnd').value = today;
document.getElementById('runStart').value = threeMonthsAgo;

async function startJob(endpoint, body, label) {
    const statusEl = document.getElementById('jobStatus');
    const textEl = document.getElementById('jobStatusText');
    const resultEl = document.getElementById('jobResult');
    statusEl.className = 'job-status';
    statusEl.classList.remove('hidden');
    textEl.textContent = `${label} — starting...`;
    resultEl.innerHTML = '';

    try {
        const res = await api(endpoint, { method: 'POST', body: JSON.stringify(body) });
        textEl.textContent = `${label} — running (job: ${res.job_id})`;
        pollJob(res.job_id, label);
    } catch (e) {
        statusEl.className = 'job-status error';
        textEl.textContent = `${label} — failed`;
        resultEl.innerHTML = `<code>${e.message}</code>`;
    }
}

async function pollJob(jobId, label, interval = 3000) {
    const statusEl = document.getElementById('jobStatus');
    const textEl = document.getElementById('jobStatusText');
    const resultEl = document.getElementById('jobResult');

    const poll = async () => {
        try {
            const job = await api(`/jobs/${jobId}`);
            if (job.status === 'running') {
                textEl.textContent = `${label} — running...`;
                setTimeout(poll, interval);
            } else if (job.status === 'done') {
                statusEl.className = 'job-status done';
                textEl.textContent = `${label} — complete ✓`;
                resultEl.innerHTML = `<pre style="font-size:13px;color:var(--text-secondary);margin-top:8px">${JSON.stringify(job.result, null, 2)}</pre>`;
            } else {
                statusEl.className = 'job-status error';
                textEl.textContent = `${label} — error`;
                resultEl.innerHTML = `<code>${job.error}</code>`;
            }
        } catch (e) {
            statusEl.className = 'job-status error';
            textEl.textContent = `${label} — polling error`;
            resultEl.innerHTML = `<code>${e.message}</code>`;
        }
    };
    setTimeout(poll, interval);
}

document.getElementById('runForm').addEventListener('submit', e => {
    e.preventDefault();
    const body = {
        username: document.getElementById('runUsername').value.replace('@', ''),
        start: document.getElementById('runStart').value,
        end: document.getElementById('runEnd').value,
        windows: document.getElementById('runWindows').value,
        headless: document.getElementById('runHeadless').value === 'true',
        max_workers: 1,
    };
    startJob(`/bloggers/${body.username}/run`, body, 'Full Pipeline');
});

document.getElementById('crawlOnlyBtn').addEventListener('click', () => {
    const body = {
        username: document.getElementById('runUsername').value.replace('@', ''),
        start: document.getElementById('runStart').value,
        end: document.getElementById('runEnd').value,
        headless: document.getElementById('runHeadless').value === 'true',
    };
    if (!body.username) return alert('Enter a username');
    startJob(`/bloggers/${body.username}/crawl`, body, 'Crawl');
});

document.getElementById('analyzeOnlyBtn').addEventListener('click', () => {
    const username = document.getElementById('runUsername').value.replace('@', '');
    if (!username) return alert('Enter a username');
    startJob(`/bloggers/${username}/analyze`, { username }, 'Analyze');
});

document.getElementById('backtestOnlyBtn').addEventListener('click', () => {
    const username = document.getElementById('runUsername').value.replace('@', '');
    if (!username) return alert('Enter a username');
    const body = { username, windows: document.getElementById('runWindows').value, max_workers: 1 };
    startJob(`/bloggers/${username}/backtest`, body, 'Backtest');
});

document.getElementById('scoreOnlyBtn').addEventListener('click', () => {
    const username = document.getElementById('runUsername').value.replace('@', '');
    if (!username) return alert('Enter a username');
    // Score is synchronous (fast)
    const statusEl = document.getElementById('jobStatus');
    statusEl.className = 'job-status';
    statusEl.classList.remove('hidden');
    document.getElementById('jobStatusText').textContent = 'Scoring...';
    document.getElementById('jobResult').innerHTML = '';
    api(`/bloggers/${username}/score`).then(score => {
        statusEl.className = 'job-status done';
        document.getElementById('jobStatusText').textContent = `Score: ${score.overall_score}/100 (${score.grade})`;
        document.getElementById('jobResult').innerHTML = `<pre style="font-size:13px;color:var(--text-secondary);margin-top:8px">Sample: ${score.sample_size} results</pre>`;
    }).catch(e => {
        statusEl.className = 'job-status error';
        document.getElementById('jobStatusText').textContent = 'Score — error';
        document.getElementById('jobResult').innerHTML = `<code>${e.message}</code>`;
    });
});

// === Reports (redesigned) ===
let allBloggerReports = [];
let allLeaderboardReports = [];

async function loadReports() {
    const grid1 = document.getElementById('bloggerReportsGrid');
    const grid2 = document.getElementById('leaderboardReportsGrid');
    grid1.innerHTML = '<div class="empty-state">Loading...</div>';
    grid2.innerHTML = '';
    try {
        const data = await api('/reports');
        allBloggerReports = data.blogger || [];
        allLeaderboardReports = data.leaderboard || [];
        renderBloggerReports();
        renderLeaderboardReports();
    } catch (e) {
        grid1.innerHTML = `<div class="empty-state">Error: ${e.message}</div>`;
    }
}

function renderBloggerReports() {
    const grid = document.getElementById('bloggerReportsGrid');
    const search = (document.getElementById('reportSearch').value || '').toLowerCase();
    const filtered = allBloggerReports.filter(r =>
        (r.username || r.name).toLowerCase().includes(search)
    );
    if (!filtered.length) {
        grid.innerHTML = '<div class="empty-state">No blogger reports found</div>';
        return;
    }
    grid.innerHTML = filtered.map(r => {
        const date = r.modified ? r.modified.slice(0, 16).replace('T', ' ') : '';
        const hasScore = r.score !== undefined;
        const scoreHtml = hasScore
            ? `<div class="report-card-score">
                <span class="report-score-value">${r.score.toFixed(1)}</span>
                <span class="report-score-label">/ 100</span>
                ${r.grade ? `<span class="grade-badge grade-${r.grade}" style="margin-left:8px">${r.grade}</span>` : ''}
               </div>`
            : '';
        const statsHtml = hasScore ? `
            <div class="report-card-stats">
                <div class="report-stat"><span class="report-stat-value">${r.sample_size || 0}</span><span class="report-stat-label">Sample</span></div>
                <div class="report-stat"><span class="report-stat-value">${r.prediction_count || 0}</span><span class="report-stat-label">Predictions</span></div>
                <div class="report-stat"><span class="report-stat-value">${r.result_count || 0}</span><span class="report-stat-label">Results</span></div>
            </div>` : '';
        return `<div class="report-card">
            <div class="report-card-header">
                <span class="report-card-title">@${r.username || r.name}</span>
                <span class="report-card-type ${r.type}">${r.type}</span>
            </div>
            ${scoreHtml}
            ${statsHtml}
            <div class="report-card-footer">
                <span class="report-date">${date}</span>
                <div class="report-actions">
                    <button class="report-btn" onclick="viewReport('${r.name}')">View</button>
                </div>
            </div>
        </div>`;
    }).join('');
}

function renderLeaderboardReports() {
    const grid = document.getElementById('leaderboardReportsGrid');
    if (!allLeaderboardReports.length) {
        grid.innerHTML = '<div class="empty-state">No leaderboard reports yet</div>';
        return;
    }
    grid.innerHTML = allLeaderboardReports.map(r => {
        const date = r.modified ? r.modified.slice(0, 16).replace('T', ' ') : '';
        return `<div class="report-card leaderboard-card">
            <div class="report-card-header">
                <span class="report-card-title">Leaderboard Snapshot</span>
                <span class="report-card-type ${r.type}">${r.type}</span>
            </div>
            <div class="report-card-stats">
                <div class="report-stat"><span class="report-stat-value">${r.blogger_count || '—'}</span><span class="report-stat-label">Bloggers</span></div>
                ${r.top_blogger ? `<div class="report-stat"><span class="report-stat-value">@${r.top_blogger}</span><span class="report-stat-label">Top Blogger</span></div>` : ''}
                ${r.top_score !== undefined && r.top_score !== null ? `<div class="report-stat"><span class="report-stat-value">${r.top_score.toFixed(1)}</span><span class="report-stat-label">Top Score</span></div>` : ''}
            </div>
            <div class="report-card-footer">
                <span class="report-date">${date}</span>
                <div class="report-actions">
                    <button class="report-btn" onclick="viewReport('${r.name}')">View</button>
                </div>
            </div>
        </div>`;
    }).join('');
}

// Report tabs
document.querySelectorAll('.report-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.report-tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.report-tab-content').forEach(c => c.classList.add('hidden'));
        btn.classList.add('active');
        document.getElementById(`rtab-${btn.dataset.rtab}`).classList.remove('hidden');
    });
});

// Search
document.getElementById('reportSearch').addEventListener('input', renderBloggerReports);

// Report viewer modal
async function viewReport(filename) {
    const modal = document.getElementById('reportModal');
    const title = document.getElementById('modalTitle');
    const body = document.getElementById('modalBody');
    title.textContent = filename;
    body.innerHTML = '<div class="empty-state">Loading...</div>';
    modal.classList.remove('hidden');
    try {
        const data = await api(`/reports/${encodeURIComponent(filename)}`);
        if (data.type === 'md') {
            body.innerHTML = `<div class="md-content">${renderMarkdown(data.content)}</div>`;
        } else {
            // JSON: pretty-print with syntax highlighting
            try {
                const parsed = JSON.parse(data.content);
                body.innerHTML = `<div class="json-content">${syntaxHighlight(JSON.stringify(parsed, null, 2))}</div>`;
            } catch {
                body.innerHTML = `<pre class="json-content">${data.content}</pre>`;
            }
        }
    } catch (e) {
        body.innerHTML = `<div class="empty-state">Error: ${e.message}</div>`;
    }
}

// Simple markdown renderer
function renderMarkdown(md) {
    let html = md
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/^# (.+)$/gm, '<h1>$1</h1>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/^- (.+)$/gm, '<li>$1</li>')
        .replace(/(<li>.+<\/li>\n?)+/g, m => `<ul>${m}</ul>`)
        .replace(/^\|(.+)\|$/gm, m => {
            const cells = m.split('|').filter(c => c.trim());
            if (cells.every(c => /^[\s-:]+$/.test(c))) return ''; // separator row
            const tag = m.includes('---') ? 'th' : 'td';
            return `<tr>${cells.map(c => `<${tag}>${c.trim()}</${tag}>`).join('')}</tr>`;
        })
        .replace(/(<tr>[\s\S]+?<\/tr>\n?)+/g, m => `<table>${m}</table>`)
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\n{2,}/g, '</p><p>')
        .replace(/^(?!<)/gm, '<p>$&')
        .replace(/([^>])$/gm, '$1</p>');
    return html.replace(/<p><\/p>/g, '');
}

// JSON syntax highlight
function syntaxHighlight(json) {
    return json
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/("(?:\\.|[^"\\])*"\s*:)/g, '<span class="json-key">$1</span>')
        .replace(/:\s*("(?:\\.|[^"\\])*")/g, ': <span class="json-string">$1</span>')
        .replace(/:\s*(-?\d+\.?\d*)/g, ': <span class="json-number">$1</span>')
        .replace(/:\s*(true|false|null)/g, ': <span class="json-boolean">$1</span>');
}

// Modal close
document.getElementById('modalClose').addEventListener('click', () => {
    document.getElementById('reportModal').classList.add('hidden');
});
document.getElementById('reportModal').addEventListener('click', e => {
    if (e.target.id === 'reportModal') {
        document.getElementById('reportModal').classList.add('hidden');
    }
});

document.getElementById('refreshLeaderboard').addEventListener('click', loadLeaderboard);
document.getElementById('refreshReports').addEventListener('click', loadReports);

// === Init ===
checkSession();
loadLeaderboard();
