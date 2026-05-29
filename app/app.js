/* ── Peace Room — Frontend App v3 ───────────────────── */

// No hardcoded categories — solutions render dynamically from data.activeSolutions

const MOMENTUM_CONFIG = {
  advancing: { icon: '🟢', label: 'Net Positive', cls: 'momentum-advancing' },
  stable:    { icon: '🟡', label: 'Mixed Signals', cls: 'momentum-stable' },
  stalling:  { icon: '🔴', label: 'Net Negative', cls: 'momentum-stalling' },
};

const DIRECTION_LABELS = {
  advancing: 'Advancing',
  stable:    'Stable',
  stalling:  'Stalling',
};

/* ── Helpers ─────────────────────────────────────────── */
function parseDate(dateStr) {
  if (!dateStr) return null;
  let d = new Date(dateStr);
  if (!isNaN(d.getTime())) return d;
  // Normalize "Wednesday, April 29, 2026 - 10:00" -> "April 29, 2026 10:00"
  const normalized = dateStr
    .replace(/^\w+,?\s*/, '')       // strip day-of-week
    .replace(/\s+-\s+/, ' ');       // replace " - " with space
  d = new Date(normalized);
  if (!isNaN(d.getTime())) return d;
  return null;
}

function formatTime(dateStr) {
  const d = parseDate(dateStr);
  if (!d) return 'recent';
  const now = new Date();
  const diffMs = now - d;
  const diffHrs = diffMs / 3600000;

  if (diffHrs < 1) {
    const mins = Math.floor(diffMs / 60000);
    return mins < 1 ? 'now' : `${mins}m`;
  }
  if (diffHrs < 24) return `${Math.floor(diffHrs)}h`;
  const days = Math.floor(diffHrs / 24);
  if (days < 7) return `${days}d`;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function formatEventTime(dateStr) {
  const d = parseDate(dateStr);
  if (!d) return '—';
  const h = d.getUTCHours();
  const m = String(d.getUTCMinutes()).padStart(2, '0');
  return `${h}:${m}`;
}

/* ── Data Loading ────────────────────────────────────── */
let data = null;
let activityFeedEvents = [];
let feedShowing = 12;

async function loadData() {
  // Load AI-generated data.json (deployed with the site)
  try {
    const res = await fetch('./data.json');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
    renderAll(data);
  } catch (err) {
    console.warn('data.json unavailable, falling back to solutions.json:', err);
    try {
      const res = await fetch('solutions.json');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
      renderAll(data);
    } catch (fallbackErr) {
      console.error('Failed to load data:', fallbackErr);
      document.getElementById('momentumSummary').textContent = 'Failed to load data. Retry later.';
    }
  }
}

/* ── Classification Warning ──────────────────────────── */
function renderClassificationWarning(aiHealth) {
  const container = document.getElementById('classificationWarning');
  if (!container) return;
  const method = aiHealth?.classificationMethod;
  if (method === 'keyword-fallback') {
    container.style.display = 'flex';
    container.innerHTML = `
      <span style="font-size:18px">⚠️</span>
      <div>
        <strong>Keyword Fallback Active</strong><br>
        <span style="font-size:12px;color:var(--text-muted)">
          AI classification was skipped or failed. Articles are classified by keyword matching only.
          Accuracy may be lower than normal.
        </span>
      </div>
    `;
  } else {
    container.style.display = 'none';
  }
}

/* ── Momentum Banner ─────────────────────────────────── */
function renderMomentum(momentum) {
  if (!momentum) return;
  const banner = document.getElementById('momentumBanner');
  const cfg = MOMENTUM_CONFIG[momentum.direction] || MOMENTUM_CONFIG.stable;
  banner.className = `momentum-banner ${cfg.cls}`;
  document.getElementById('momentumIcon').textContent = cfg.icon;
  document.getElementById('momentumLabel').textContent = cfg.label;
  document.getElementById('momentumSummary').textContent = momentum.summary || '';
}

/* ── Activity Feed (global) ──────────────────────────── */
function buildActivityFeed() {
  // Collect all events across all solutions, sort by date desc
  const all = [];
  (data.solutions || []).forEach(sol => {
    (sol.events || []).forEach(ev => {
      all.push({ ...ev, solutionId: sol.id, solutionName: sol.name });
    });
  });
  all.sort((a, b) => {
    const da = parseDate(a.date) || new Date(0);
    const db = parseDate(b.date) || new Date(0);
    return db - da;
  });
  activityFeedEvents = all;
  renderActivityFeed();
}

function renderActivityFeed() {
  const container = document.getElementById('activityFeed');
  const show = activityFeedEvents.slice(0, feedShowing);
  container.innerHTML = '';

  show.forEach(ev => {
    const item = document.createElement('div');
    item.className = `activity-item sentiment-${ev.sentiment || 'neutral'}`;
    item.innerHTML = `
      <span class="activity-time">${formatTime(ev.date)}</span>
      <span class="activity-solution">${ev.solutionId}</span>
      ${ev.link ? `<a href="${ev.link}" target="_blank" rel="noopener" class="activity-link">${ev.text}</a>` : `<span class="activity-text">${ev.text}</span>`}
    `;
    container.appendChild(item);
  });

  // Toggle more
  const moreBtn = document.getElementById('showMoreActivity');
  if (feedShowing >= activityFeedEvents.length) {
    moreBtn.style.display = 'none';
  } else {
    moreBtn.style.display = 'block';
    const extra = Math.min(12, activityFeedEvents.length - feedShowing);
    moreBtn.textContent = `Show ${extra} more events…`;
  }
}

document.getElementById('showMoreActivity')?.addEventListener('click', () => {
  feedShowing += 12;
  renderActivityFeed();
});

/* ── Solution Cards ──────────────────────────────────── */
function createSolutionCard(solution) {
  const card = document.createElement('div');
  card.className = `solution-card ${solution.direction}`;

  // Top row
  const top = document.createElement('div');
  top.className = 'card-top';
  top.innerHTML = `
    <span class="card-icon">${solution.icon}</span>
    <span class="card-name">${solution.name}</span>
    <span class="card-direction ${solution.direction}">${DIRECTION_LABELS[solution.direction] || solution.direction}</span>
  `;

  // Phase bar
  const phaseBar = document.createElement('div');
  phaseBar.className = 'phase-bar';
  const phases = solution.phases || [];
  const idx = solution.phaseIndex || 0;
  phases.forEach((p, i) => {
    const seg = document.createElement('div');
    seg.className = 'phase-segment' + (i < idx ? ' filled' : '') + (i === idx ? ' current' : '');
    phaseBar.appendChild(seg);
  });
  const plabel = document.createElement('span');
  plabel.className = 'phase-label';
  plabel.textContent = phases[idx] ? `"${phases[idx]}"` : '';
  phaseBar.appendChild(plabel);

  // Metric + summary
  const row = document.createElement('div');
  row.className = 'card-row';
  const kv = solution.keyMetric || {};
  const metric = document.createElement('div');
  metric.className = 'card-metric';
  let valHtml = `${kv.value || '—'}`;
  if (kv.total) valHtml += ` / ${kv.total}`;
  if (kv.unit) valHtml += `<small style="font-size:11px;color:var(--text-muted)"> ${kv.unit}</small>`;
  metric.innerHTML = `<span class="value">${valHtml}</span><span class="label">${kv.label || ''}</span>`;

  const summary = document.createElement('div');
  summary.className = 'card-summary';
  summary.textContent = solution.summary || '';
  row.appendChild(metric);
  row.appendChild(summary);

  card.appendChild(top);
  card.appendChild(phaseBar);
  card.appendChild(row);

  // Recent Events (inline list)
  const events = solution.events || [];
  if (events.length) {
    const evDiv = document.createElement('div');
    evDiv.className = 'card-events';
    const evTitle = document.createElement('div');
    evTitle.className = 'card-events-title';
    evTitle.textContent = `Recent Events (${events.length})`;
    evDiv.appendChild(evTitle);

    // Show top 3, with toggle for more
    const show = 3;
    events.slice(0, show).forEach(ev => {
      const item = document.createElement('div');
      item.className = 'card-event';
      item.innerHTML = `
        <span class="card-event-dot sentiment-${ev.sentiment || 'neutral'}"></span>
        <span class="card-event-time">${formatTime(ev.date)}</span>
        ${ev.link ? `<a href="${ev.link}" target="_blank" rel="noopener" class="card-event-text">${ev.text}</a>` : `<span class="card-event-text">${ev.text}</span>`}
      `;
      evDiv.appendChild(item);
    });

    // Toggle more events
    if (events.length > show) {
      const toggle = document.createElement('div');
      toggle.className = 'card-events-toggle';
      toggle.textContent = `Show ${events.length - show} more…`;
      toggle.addEventListener('click', () => {
        // Render all events
        evDiv.querySelectorAll('.card-event, .card-events-toggle').forEach(el => el.remove());
        events.forEach(ev => {
          const item = document.createElement('div');
          item.className = 'card-event';
          item.innerHTML = `
            <span class="card-event-dot sentiment-${ev.sentiment || 'neutral'}"></span>
            <span class="card-event-time">${formatTime(ev.date)}</span>
            ${ev.link ? `<a href="${ev.link}" target="_blank" rel="noopener" class="card-event-text">${ev.text}</a>` : `<span class="card-event-text">${ev.text}</span>`}
          `;
          evDiv.appendChild(item);
        });
        evDiv.appendChild(toggle);
        toggle.textContent = 'Show less';
        toggle.addEventListener('click', () => {
          // Re-render with limit (reload approach)
          loadData();
        });
      });
      evDiv.appendChild(toggle);
    }
    card.appendChild(evDiv);
  }

  // Key Players
  if (solution.stakeholders && solution.stakeholders.length) {
    const playersDiv = document.createElement('div');
    playersDiv.className = 'card-players';
    const pTitle = document.createElement('div');
    pTitle.className = 'card-players-title';
    pTitle.textContent = 'Key Players';
    playersDiv.appendChild(pTitle);

    // Render as comma-separated inline list
    const playersRow = document.createElement('div');
    playersRow.className = 'card-players-row';

    solution.stakeholders.forEach((p, i) => {
      if (i > 0) {
        const comma = document.createElement('span');
        comma.className = 'card-players-sep';
        comma.textContent = ',';
        playersRow.appendChild(comma);
      }
      const link = document.createElement('a');
      link.className = 'card-player-chip';
      link.href = `mailto:${p.email}`;
      link.title = `${p.name} — ${p.org}`;
      link.textContent = p.name;
      playersRow.appendChild(link);
    });
    playersDiv.appendChild(playersRow);
    card.appendChild(playersDiv);
  }

  return card;
}

/* ── Render All ──────────────────────────────────────── */
function renderAll(data) {
  renderMomentum(data.overallMomentum);
  renderClassificationWarning(data.aiHealth);

  // Update timestamp
  if (data.lastUpdated) {
    const ts = document.getElementById('lastUpdated');
    ts.textContent = `Updated ${formatTime(data.lastUpdated)} ago`;
  }

  // Version tag — show app version + AI version if available
  const vt = document.getElementById('versionTag');
  if (vt) {
    const appVersion = 'v0.3.0';
    const aiVersion = data.aiVersion ? ` AI ${data.aiVersion}` : '';
    vt.textContent = `${appVersion}${aiVersion}`;
  }

  // Activity feed
  buildActivityFeed();

  // Solution cards — all active solutions in a single grid
  const grid = document.getElementById('solutionsGrid');
  if (grid) grid.innerHTML = '';
  const activeIds = data.activeSolutions || data.solutions.map(s => s.id);
  (data.solutions || [])
    .filter(solution => activeIds.includes(solution.id))
    .sort((a, b) => b.keyMetric.value - a.keyMetric.value)
    .slice(0, 8)
    .forEach(solution => {
      const card = createSolutionCard(solution);
      if (grid) grid.appendChild(card);
    });
}

/* ── Boot ────────────────────────────────────────────── */
loadData();

// Auto-refresh every 15 minutes (browser caches 3h, so this catches new data)
const REFRESH_INTERVAL = 15 * 60 * 1000;
setInterval(() => {
  console.log('[Peace Room] Auto-refreshing…');
  loadData();
}, REFRESH_INTERVAL);

// Version tag is now rendered in renderAll() for access to data.aiVersion
