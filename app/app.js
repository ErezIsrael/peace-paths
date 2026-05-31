/* ── Peace Paths — Frontend App v4 ──────────────────── */

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
const FEED_MAX = 5;
let feedShowing = FEED_MAX;

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
  if (feedShowing < activityFeedEvents.length) {
    feedShowing += 12;
    renderActivityFeed();
  } else if (feedShowing === FEED_MAX) {
    feedShowing = activityFeedEvents.length;
    renderActivityFeed();
  }
});

/* ── Peace Path Progress Track ───────────────────────── */
function buildPeacePath(solution) {
  const phases = solution.phases || [];
  const idx = solution.phaseIndex || 0;
  const total = phases.length;
  if (total === 0) return null;

  const pct = Math.round(((idx + 1) / total) * 100);

  const track = document.createElement('div');
  track.className = 'peace-path';

  // Rail (horizontal line)
  const rail = document.createElement('div');
  rail.className = 'peace-path__rail';

  const fill = document.createElement('div');
  fill.className = 'peace-path__rail-fill';
  fill.style.width = `${(idx / (total - 1)) * 100}%`;

  const dash = document.createElement('div');
  dash.className = 'peace-path__rail-dash';
  dash.style.width = `${(1 - idx / (total - 1)) * 100}%`;

  rail.appendChild(fill);
  rail.appendChild(dash);
  track.appendChild(rail);

  // Milestone nodes
  const nodes = document.createElement('div');
  nodes.className = 'peace-path__nodes';

  phases.forEach((p, i) => {
    const node = document.createElement('div');
    node.className = 'peace-path__node';

    const dot = document.createElement('div');
    if (i < idx) dot.className = 'peace-path__dot done';
    else if (i === idx) dot.className = 'peace-path__dot active';
    else dot.className = 'peace-path__dot future';

    const label = document.createElement('div');
    label.className = 'peace-path__label';
    if (i < idx) label.classList.add('done');
    else if (i === idx) label.classList.add('active');
    else label.classList.add('future');

    // Truncate long phase names for display
    const short = p.length > 28 ? p.slice(0, 26) + '…' : p;
    label.textContent = `${i + 1}. ${short}`;
    label.title = p; // full name on hover

    node.appendChild(dot);
    node.appendChild(label);
    nodes.appendChild(node);
  });

  track.appendChild(nodes);

  // Percentage badge
  const pctBadge = document.createElement('div');
  pctBadge.className = 'peace-path__pct';
  pctBadge.textContent = `${pct}%`;
  track.appendChild(pctBadge);

  return track;
}

/* ── Solution Cards ──────────────────────────────────── */
function createSolutionCard(solution) {
  const card = document.createElement('div');
  card.className = `solution-card ${solution.direction}`;

  // Top row: icon, name, metric, direction
  const top = document.createElement('div');
  top.className = 'card-top';
  const kv = solution.keyMetric || {};
  const eventsCount = (solution.events || []).length;
  let valHtml = eventsCount ? `${eventsCount}` : `${kv.value || '—'}`;
  if (kv.total && !eventsCount) valHtml += ` / ${kv.total}`;
  if (kv.unit && !eventsCount) valHtml += `<small style="font-size:11px;color:var(--text-muted)"> ${kv.unit}</small>`;
  const metricHtml = `<span class="card-metric"><span class="card-metric-value">${valHtml}</span><span class="card-metric-label">${kv.label || ''}</span></span>`;
  top.innerHTML = `
    <span class="card-icon">${solution.icon}</span>
    <span class="card-name">${solution.name}</span>
    ${metricHtml}
    <span class="card-direction ${solution.direction}">${DIRECTION_LABELS[solution.direction] || solution.direction}</span>
  `;

  // Peace Path progress track
  card.appendChild(top);
  card.appendChild(buildPeacePath(solution));

  // Events list (sorted newest-first)
  const events = (solution.events || []).slice().sort((a, b) => {
    const da = parseDate(a.date) || new Date(0);
    const db = parseDate(b.date) || new Date(0);
    return db - da;
  });

  if (events.length) {
    const evDiv = document.createElement('div');
    evDiv.className = 'card-events';

    // Show top 3, with toggle for more
    const SENTIMENT_LABELS = { positive: 'Peace', neutral: 'Neutral', negative: 'War' };
    const show = 3;
    events.slice(0, show).forEach(ev => {
      const src = ev.source ? ` <span class="card-event-source">(${ev.source})</span>` : '';
      const sentLabel = ev.sentiment ? SENTIMENT_LABELS[ev.sentiment] || ev.sentiment : '';
      const item = document.createElement('div');
      item.className = 'card-event';
      item.innerHTML = `
        <span class="card-event-dot sentiment-${ev.sentiment || 'neutral'}"></span>
        ${sentLabel ? `<span class="card-event-sentiment sentiment-${ev.sentiment}">${sentLabel}</span>` : ''}
        <span class="card-event-time">${formatTime(ev.date)}</span>
        ${ev.link ? `<a href="${ev.link}" target="_blank" rel="noopener" class="card-event-text">${ev.text}</a>` : `<span class="card-event-text">${ev.text}</span>`}
        ${src}
      `;
      evDiv.appendChild(item);
    });

    // Toggle more events
    if (events.length > show) {
      const toggle = document.createElement('div');
      toggle.className = 'card-events-toggle';
      toggle.textContent = `Show ${events.length - show} more…`;
      toggle.addEventListener('click', () => {
        evDiv.querySelectorAll('.card-event, .card-events-toggle').forEach(el => el.remove());
        events.forEach(ev => {
          const src = ev.source ? ` <span class="card-event-source">(${ev.source})</span>` : '';
          const sentLabel = ev.sentiment ? SENTIMENT_LABELS[ev.sentiment] || ev.sentiment : '';
          const item = document.createElement('div');
          item.className = 'card-event';
          item.innerHTML = `
            <span class="card-event-dot sentiment-${ev.sentiment || 'neutral'}"></span>
            ${sentLabel ? `<span class="card-event-sentiment sentiment-${ev.sentiment}">${sentLabel}</span>` : ''}
            <span class="card-event-time">${formatTime(ev.date)}</span>
            ${ev.link ? `<a href="${ev.link}" target="_blank" rel="noopener" class="card-event-text">${ev.text}</a>` : `<span class="card-event-text">${ev.text}</span>`}
            ${src}
          `;
          evDiv.appendChild(item);
        });
        evDiv.appendChild(toggle);
        toggle.textContent = 'Show less';
        toggle.addEventListener('click', () => {
          loadData();
        });
      });
      evDiv.appendChild(toggle);
    }
    card.appendChild(evDiv);
  }

  // Key Players / Stakeholders
  if (solution.stakeholders && solution.stakeholders.length) {
    const playersDiv = document.createElement('div');
    playersDiv.className = 'card-players';
    const pTitle = document.createElement('div');
    pTitle.className = 'card-players-title';
    pTitle.textContent = 'Key Players';
    playersDiv.appendChild(pTitle);

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
      // Contact can be email, URL, or webform
      if (p.contact.startsWith('mailto:')) {
        link.href = p.contact;
      } else if (p.contact.includes('@')) {
        link.href = `mailto:${p.contact}`;
      } else {
        link.href = p.contact;
        link.target = '_blank';
        link.rel = 'noopener';
      }
      link.title = `${p.name} — ${p.role}`;
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

/* ── Info Modal ──────────────────────────────────────── */
document.getElementById('infoBtn')?.addEventListener('click', (e) => {
  e.preventDefault();
  const overlay = document.getElementById('modalOverlay');
  const content = document.getElementById('modalContent');
  content.innerHTML = `
    <h2>How Peace Paths Works</h2>

    <h3>📡 Data Collection</h3>
    <p>We monitor <strong>60 RSS feeds</strong> across the Middle East — a curated selection of news outlets, think tanks, and human rights organizations. Sources include:</p>
    <ul>
      <li><strong>News agencies:</strong> BBC, Al Jazeera, France24, Reuters, The Guardian, NYT, Le Monde, Haaretz, JPost, and more</li>
      <li><strong>Think tanks:</strong> Crisis Group, MERIP, The Diplomat, The Conversation, Global Policy Forum</li>
      <li><strong>Human rights:</strong> Amnesty International, Iran Human Rights, Center for Human Rights in Iran</li>
      <li><strong>Regional outlets:</strong> PNN, 972mag, Radio Free Europe, Iran International, Global Voices</li>
    </ul>
    <p>Feeds are fetched daily. Articles are collected from a 7-day rolling window.</p>

    <h3>🤖 AI Classification</h3>
    <p>Each article is analyzed by a large language model (LLM) that:</p>
    <ul>
      <li><strong>Classifies</strong> it into a relevant peace initiative category (e.g., "Ceasefire Negotiations", "Humanitarian Aid")</li>
      <li><strong>Assigns a sentiment:</strong></li>
    </ul>
    <div style="display:flex;gap:16px;margin:8px 0 12px;font-size:13px">
      <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;border-radius:50%;background:#4ade80;display:inline-block"></span> <strong>Peace</strong> — positive/constructive</span>
      <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;border-radius:50%;background:#94a3b8;display:inline-block"></span> <strong>Neutral</strong> — factual/status quo</span>
      <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;border-radius:50%;background:#f87171;display:inline-block"></span> <strong>War</strong> — negative/escalating</span>
    </div>
    <p>When the AI is unavailable, articles are classified by keyword matching (lower accuracy).</p>

    <h3>🛤️ Peace Path Progress Track</h3>
    <p>Each initiative shows a <strong>milestone roadmap</strong> — like a bridge being built. Green dots are completed phases, the pulsing blue dot is the current phase, and faded dots are future milestones yet to reach.</p>
    <p>The percentage badge shows overall progress. Hover over any milestone to see its full name.</p>

    <h3>📊 Phase & Momentum Scoring</h3>
    <p>Each peace initiative has a <strong>phase progression model</strong> (e.g., "Crisis" → "Negotiations" → "Agreement" → "Implementation"). The current phase is determined by the AI based on article content.</p>
    <p><strong>Momentum</strong> (Advancing / Stable / Stalling) is computed from the balance of positive vs. negative events across all initiatives.</p>
    <p>Event counts reflect actual articles classified in each category — not AI estimates.</p>

    <h3>⚠️ Limitations</h3>
    <ul>
      <li>Classification is automated and may misclassify articles</li>
      <li>Sentiment labels reflect article tone, not ground truth</li>
      <li>Phase progressions are heuristic, not verified</li>
      <li>This tool is experimental and for informational purposes only</li>
    </ul>
  `;
  overlay.classList.add('active');
});

// Close modal on close button click
document.getElementById('modalClose')?.addEventListener('click', () => {
  document.getElementById('modalOverlay').classList.remove('active');
});

// Close modal when clicking outside the content
document.getElementById('modalOverlay')?.addEventListener('click', (e) => {
  if (e.target === e.currentTarget) {
    document.getElementById('modalOverlay').classList.remove('active');
  }
});

/* ── Boot ────────────────────────────────────────────── */
loadData();

// Auto-refresh every 15 minutes (browser caches 3h, so this catches new data)
const REFRESH_INTERVAL = 15 * 60 * 1000;
setInterval(() => {
  console.log('[Peace Paths] Auto-refreshing…');
  loadData();
}, REFRESH_INTERVAL);

// Version tag is now rendered in renderAll() for access to data.aiVersion












