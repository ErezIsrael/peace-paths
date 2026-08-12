/* ── Peace Paths — Frontend App v5 (Narrative Pipeline) ─── */

// No hardcoded categories — solutions render dynamically from data.activeSolutions

const MOMENTUM_CONFIG_KEYS = {
  advancing: 'momentumAdvancing',
  stable: 'momentumStable',
  stalling: 'momentumStalling',
};

const MOMENTUM_ICONS = {
  advancing: { icon: '🟢', cls: 'momentum-advancing' },
  stable:    { icon: '🟡', cls: 'momentum-stable' },
  stalling:  { icon: '🔴', cls: 'momentum-stalling' },
};

function getDirectionLabel(key) {
  return t(key) || key;
}

// Current language
let currentLang = 'he';
let translations = {};

/* ── Translation System ───────────────────────────────── */
async function loadTranslations() {
  try {
    const res = await fetch('./translations.json');
    if (res.ok) {
      translations = await res.json();
    }
  } catch (e) {
    console.warn('translations.json unavailable:', e);
    translations = { en: {}, he: {}, ar: {} };
  }
}

function t(key) {
  const lang = translations[currentLang] || {};
  return lang[key] || translations.en?.[key] || key;
}

function getLangDirection() {
  return (currentLang === 'he' || currentLang === 'ar') ? 'rtl' : 'ltr';
}

function applyLanguage(lang) {
  currentLang = lang;
  document.documentElement.lang = lang;
  document.documentElement.dir = getLangDirection();
  localStorage.setItem('peace-paths-lang', lang);
  document.title = t('siteTitle');

  // Update language switcher
  document.querySelectorAll('.lang-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.lang === lang);
  });

  // Update static UI elements
  const logoText = document.getElementById('logoText');
  if (logoText) logoText.textContent = t('siteTitle');
  const tagline = document.querySelector('.tagline');
  if (tagline) tagline.textContent = t('tagline');
  const infoBtn = document.getElementById('infoBtn');
  if (infoBtn) infoBtn.textContent = `ℹ ${t('howItWorks')}`;
  const modalClose = document.getElementById('modalClose');
  if (modalClose) modalClose.textContent = t('close');

  // Section headers
  const sectionHeaders = document.querySelectorAll('.section-header');
  if (sectionHeaders[0]) {
    // Recent Activity — keep the live dot
    const dot = sectionHeaders[0].querySelector('.live-dot');
    sectionHeaders[0].textContent = '';
    if (dot) sectionHeaders[0].appendChild(dot);
    sectionHeaders[0].appendChild(document.createTextNode(' ' + (t('recentActivity') || 'Recent Activity')));
  }
  if (sectionHeaders[1]) sectionHeaders[1].textContent = t('solutions') || 'Solutions';

  // Footer
  const footer = document.querySelector('.footer');
  if (footer) {
    const ps = footer.querySelectorAll('p');
    if (ps[0]) ps[0].textContent = t('footerDisclaimer');
    if (ps[1]) ps[1].innerHTML = t('footerData').replace('{n}', `<span id="feedCount">${data?.feedCount || 89}</span>`);
    if (ps[2]) ps[2].textContent = t('footerAlgorithmic');
  }
  // Footer legal links
  const privacyLink = document.getElementById('privacyLink');
  if (privacyLink) privacyLink.textContent = t('privacyPolicy');
  const termsLink = document.getElementById('termsLink');
  if (termsLink) termsLink.textContent = t('termsService');
  const accessibilityLink = document.getElementById('accessibilityLink');
  if (accessibilityLink) accessibilityLink.textContent = t('accessibility');
  // Other footer links
  const footerLinks = document.querySelectorAll('.footer-links .footer-link:not(#versionTag):not(#privacyLink):not(#termsLink):not(#accessibilityLink)');
  if (footerLinks[0]) footerLinks[0].textContent = t('reportBug');
  if (footerLinks[1]) footerLinks[1].textContent = t('buyCoffee');
  if (footerLinks[2]) footerLinks[2].textContent = t('contactEmail');

  // Activity "show more" button
  const moreBtn = document.getElementById('showMoreActivity');
  if (moreBtn && moreBtn.style.display !== 'none' && activityFeedEvents.length > feedShowing) {
    const extra = Math.min(12, activityFeedEvents.length - feedShowing);
    moreBtn.textContent = `${extra} ${t('showMore')}`;
  }

  // Refresh info modal if open
  const overlay = document.getElementById('modalOverlay');
  if (overlay && overlay.classList.contains('active')) {
    renderInfoModal();
  }

  // Refresh refresh badge title
  const refreshBadge = document.getElementById('refreshBadge');
  if (refreshBadge) refreshBadge.title = t('refreshTitle');

  // Re-render momentum banner (direction label + trilingual summary)
  if (data?.overallMomentum) renderMomentum(data.overallMomentum);

  // Re-render with new language
  if (data) renderAll(data);
}

function detectLanguage() {
  // Check localStorage first
  const saved = localStorage.getItem('peace-paths-lang');
  if (saved && translations[saved]) return saved;

  // Check URL param
  const params = new URLSearchParams(window.location.search);
  const urlLang = params.get('lang');
  if (urlLang && translations[urlLang]) return urlLang;

  // Check browser preference (only for he/ar, default to Hebrew otherwise)
  const browser = navigator.language.slice(0, 2);
  if (browser === 'he' || browser === 'ar') return browser;

  return currentLang;
}

/* ── Helpers ─────────────────────────────────────────── */
function parseDate(dateStr) {
  if (!dateStr) return null;
  let d = new Date(dateStr);
  if (!isNaN(d.getTime())) return d;
  const normalized = dateStr
    .replace(/^\w+,?\s*/, '')
    .replace(/\s+-\s+/, ' ');
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

/* ── Script Detection Regexes ─────────────────────────── */
const HE_BROADER = /[\u05D0-\u05EA\u05F0-\u05F4\uFB1D-\uFB4F]/; // Hebrew letters + presentation forms
const AR_BROADER = /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]/; // Arabic + extended

function hasScriptText(lang, text) {
  // Returns true if `text` contains characters in the expected script for `lang`
  if (lang === 'he') return HE_BROADER.test(text);
  if (lang === 'ar') return AR_BROADER.test(text);
  return true; // English — no script check
}

function sanitizeText(text) {
  // Handle AI pipeline producing JSON array strings like ["text1", "text2"]
  // If the string starts with [ and ends with ], extract the first element
  if (typeof text !== 'string') return text;
  const trimmed = text.trim();
  if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
    // First try standard JSON.parse
    try {
      const arr = JSON.parse(trimmed);
      if (Array.isArray(arr) && arr.length > 0) {
        return arr.filter(s => typeof s === 'string' && s.trim()).join(' · ');
      }
    } catch (e) {
      // JSON.parse failed (e.g. unescaped quotes inside strings like ארה"ב)
      // Fallback: strip outer brackets, find first '", "' separator, take first element
      const inner = trimmed.slice(1, -1).trim();
      const sepIdx = inner.indexOf('", "');
      if (sepIdx > 0) {
        let first = inner.slice(0, sepIdx);
        first = first.replace(/^"/, '').replace(/"$/, '').trim();
        if (first) return first;
      }
      // Ultimate fallback: just strip brackets and outer quotes
      return inner.replace(/^"|"$/g, '').trim();
    }
  }
  return text;
}

/* ── Multilingual Text Helper ─────────────────────────── */
function getLangText(obj, fallback) {
  if (typeof obj === 'string') return sanitizeText(obj);
  if (!obj || typeof obj !== 'object') return fallback || '';

  // If current language key exists and has content, use it
  if (obj[currentLang] && obj[currentLang].trim()) {
    const cur = sanitizeText(obj[currentLang]);
    // Guard: for RTL languages, verify the field actually contains native script
    // Catches: (a) empty fields, (b) English text stored in he/ar field,
    //         (c) pure-ASCII fallbacks that differ from en
    if (currentLang === 'he' || currentLang === 'ar') {
      if (hasScriptText(currentLang, cur)) {
        return cur; // Genuine Hebrew/Arabic text
      }
      // No native script found — this is untranslated English masquerading as he/ar
      // Fall through to English fallback below
    } else {
      return cur;
    }
  }

  // Fallback to English — but only if we're in English mode
  // In he/ar mode, if no valid translation exists, return empty to prevent English leakage
  if (currentLang === 'he' || currentLang === 'ar') {
    return fallback || '';
  }
  if (obj.en) return sanitizeText(obj.en);
  return fallback || '';
}

/* ── Data Loading ────────────────────────────────────── */
let data = null;
let activityFeedEvents = [];
const FEED_MAX = 4;
let feedShowing = FEED_MAX;

async function loadData() {
  try {
    const res = await fetch('./data.json');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
    renderAll(data);
  } catch (err) {
    console.warn('data.json unavailable, falling back to solutions.json:', err);
    try {
      const res = await fetch('./solutions.json');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
      renderAll(data);
    } catch (fallbackErr) {
      console.error('Failed to load data:', fallbackErr);
      const ms = document.getElementById('momentumSummary');
      if (ms) ms.textContent = t('error') || 'Failed to load data';
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
        <strong>${t('keywordFallback')}</strong><br>
        <span style="font-size:12px;color:var(--text-muted)">
          ${t('keywordFallbackDesc')}
        </span>
      </div>
    `;
  } else {
    container.style.display = 'none';
  }
}

/* ── Welcome / Momentum Banner ──────────────────────── */
function renderMomentum(momentum) {
  if (!momentum) return;
  const banner = document.getElementById('momentumBanner');
  const cfg = MOMENTUM_ICONS[momentum.direction] || MOMENTUM_ICONS.stable;
  banner.className = `momentum-banner ${cfg.cls}`;

  // Build counts from solutions
  const solutions = data.solutions || [];
  const counts = { advancing: 0, stable: 0, stalling: 0 };
  solutions.forEach(s => { if (counts.hasOwnProperty(s.direction)) counts[s.direction]++; });

  // Pick the right welcome text variant
  const key = momentum.direction === 'advancing' ? 'welcomeTextPositive'
            : momentum.direction === 'stalling' ? 'welcomeTextNegative'
            : 'welcomeTextMixed';
  let text = t(key)
    .replace('{advancing}', counts.advancing)
    .replace('{stable}', counts.stable)
    .replace('{stalling}', counts.stalling);

  // Render as two paragraphs (split on blank line)
  const welcomeEl = document.getElementById('welcomeText');
  const summaryEl = document.getElementById('momentumSummary');
  const parts = text.split('\n\n');
  welcomeEl.textContent = parts[0] || '';
  summaryEl.textContent = parts[1] || '';
}

/* ── Activity Feed ───────────────────────────────────── */
function buildActivityFeed() {
  const all = [];
  (data.solutions || []).forEach(sol => {
    (sol.events || []).forEach(ev => {
      // Skip events that have no usable text in the current language
      const txt = ev.text;
      let hasLangText = false;
      if (typeof txt === 'string') {
        // Plain string — check script for RTL languages
        hasLangText = (currentLang === 'en') || hasScriptText(currentLang, sanitizeText(txt));
      } else if (txt && txt[currentLang] && txt[currentLang].trim()) {
        // Has field for current lang — check script for RTL (after sanitizing JSON arrays)
        const sanitized = sanitizeText(txt[currentLang]);
        hasLangText = (currentLang === 'en') || hasScriptText(currentLang, sanitized);
      }
      // In RTL mode, do NOT fall back to English — skip untranslated events
      if (!hasLangText) return;
      all.push({ ...ev, solutionId: sol.id, solutionName: getLangText(sol.name, '') });
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
      <span class="activity-solution">${ev.solutionName}</span>
      ${ev.link ? `<a href="${ev.link}" target="_blank" rel="noopener" class="activity-link">${getLangText(ev.text)}</a>` : `<span class="activity-text">${getLangText(ev.text)}</span>`}
    `;
    container.appendChild(item);
  });

  const moreBtn = document.getElementById('showMoreActivity');
  if (feedShowing >= activityFeedEvents.length) {
    moreBtn.style.display = 'none';
  } else {
    moreBtn.style.display = 'block';
    const extra = Math.min(12, activityFeedEvents.length - feedShowing);
    moreBtn.textContent = `${extra} ${t('showMore')}`;
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

  const nodes = document.createElement('div');
  nodes.className = 'peace-path__nodes';

  // Peace path always LTR (CSS direction:ltr on .peace-path)
  const orderedPhases = phases;
  const activeIdx = idx;

  orderedPhases.forEach((p, i) => {
    const node = document.createElement('div');
    node.className = 'peace-path__node';
    const dot = document.createElement('div');
    if (i < activeIdx) dot.className = 'peace-path__dot done';
    else if (i === activeIdx) dot.className = 'peace-path__dot active';
    else dot.className = 'peace-path__dot future';
    const label = document.createElement('div');
    label.className = 'peace-path__label';
    if (i < activeIdx) label.classList.add('done');
    else if (i === activeIdx) label.classList.add('active');
    else label.classList.add('future');
    const phaseText = getLangText(p);
    const short = phaseText.length > 28 ? phaseText.slice(0, 26) + '…' : phaseText;
    label.textContent = `${i + 1}. ${short}`;
    label.title = phaseText;
    node.appendChild(dot);
    node.appendChild(label);
    nodes.appendChild(node);
  });

  track.appendChild(nodes);

  const pctBadge = document.createElement('div');
  pctBadge.className = 'peace-path__pct';
  pctBadge.textContent = `${pct}%`;
  track.appendChild(pctBadge);

  return track;
}

/* ── Type Badge ──────────────────────────────────────── */
function typeBadge(type) {
  const labels = { reporting: t('reporting'), analysis: t('analysis'), opinion: t('opinion') };
  const label = labels[type] || type;
  return `<span class="type-badge type-${type}">${label}</span>`;
}

/* ── Solution Cards (Concept J — Layered Narrative) ──── */
function createSolutionCard(solution) {
  const card = document.createElement('div');

  // Check if narrative exists (new schema)
  const hasNarrative = solution.narrative && solution.narrative.longTerm;

  if (hasNarrative) {
    card.className = `layered-card ${solution.direction}`;
    card.appendChild(buildLayeredCard(solution));
  } else {
    // Fallback: old schema
    card.className = `solution-card ${solution.direction}`;
    card.appendChild(createLegacyCardTop(solution));
    const path = buildPeacePath(solution);
    if (path) card.appendChild(path);
    const events = buildLegacyEvents(solution);
    if (events) {
      const toggle = document.createElement('div');
      toggle.className = 'lc-layer-title collapsible-toggle';
      toggle.textContent = `⚡ ${t('events')}`;
      card.appendChild(toggle);
      card.appendChild(events);
    }
  }

  // Stakeholders
  if (solution.stakeholders && solution.stakeholders.length) {
    card.appendChild(buildStakeholders(solution.stakeholders));
  }

  return card;
}

function buildLayeredCard(solution) {
  const wrapper = document.createElement('div');
  wrapper.className = 'lc-wrapper';

  // Header
  const header = document.createElement('div');
  header.className = 'lc-header';
  const kv = solution.keyMetric || {};
  const n = solution.narrative || {};
  const freshEvents = (solution.events || []).length;
  const archivedItems = (n.keyEvents || []).length + (n.keyOpinions || []).length;
  // Show fresh count; if none, show archived count with indicator
  let countDisplay = freshEvents ? `${freshEvents}` : (archivedItems ? `${archivedItems} ${t('archived')}` : (kv.value || '—'));
  const solutionName = getLangText(solution.name);
  const dirLabel = getDirectionLabel(solution.direction);
  header.innerHTML = `
    <span class="lc-icon">${solution.icon}</span>
    <span class="lc-name">${solutionName}</span>
    <span class="lc-metric">${countDisplay} <small>${getLangText(kv.label, kv.label || '')}</small></span>
    <span class="lc-direction ${solution.direction}">${dirLabel}</span>
  `;
  wrapper.appendChild(header);

  // Progress track
  const path = buildPeacePath(solution);
  if (path) wrapper.appendChild(path);

  // Shifts badges
  if (n.shifts && n.shifts.length) {
    const shiftsDiv = document.createElement('div');
    shiftsDiv.className = 'lc-shifts';
    n.shifts.slice(0, 3).forEach(s => {
      const desc = getLangText(s.desc, '');
      if (!desc) return; // Skip shifts with no translation for current language
      const badge = document.createElement('span');
      badge.className = `lc-shift-badge ${s.direction}`;
      badge.textContent = `${t('shiftDetected')}: ${desc}`;
      shiftsDiv.appendChild(badge);
    });
    wrapper.appendChild(shiftsDiv);
  }

  // Long-term Arc layer
  let longTerm = getLangText(n.longTerm, '');
  // For archived solutions, fall back to English
  if (!longTerm && !hasFreshEvents) {
    longTerm = typeof n.longTerm === 'string' ? n.longTerm : (n.longTerm.en || '');
  }
  if (longTerm) {
    const layer = document.createElement('div');
    layer.className = 'lc-layer lc-layer-context';
    layer.innerHTML = `
      <div class="lc-layer-title">📖 ${t('longTerm')}</div>
      <div class="lc-layer-text">${longTerm}</div>
    `;
    wrapper.appendChild(layer);
  }

  // Weekly AI Narrative layer (always visible)
  const hasFreshEvents = freshEvents > 0;
  let weekly = getLangText(n.weeklyHighlight, '');
  // For archived solutions, fall back to English for weekly highlight
  if (!weekly && !hasFreshEvents) {
    weekly = typeof n.weeklyHighlight === 'string' ? n.weeklyHighlight : (n.weeklyHighlight.en || '');
  }
  if (weekly) {
    const layer = document.createElement('div');
    layer.className = 'lc-layer lc-layer-signals';
    layer.innerHTML = `
      <div class="lc-layer-title">📝 ${t('weeklyHighlight')}</div>
      <div class="lc-layer-text">${weekly}</div>
    `;
    wrapper.appendChild(layer);
  }

  // This Week's Signals layer (collapsible)
  // Show keyEvents from narrative if no fresh events, or both if fresh events exist
  if (n.keyEvents && n.keyEvents.length) {
    const layer = document.createElement('div');
    layer.className = 'lc-layer lc-layer-signals';
    const title = document.createElement('div');
    title.className = 'lc-layer-title collapsible-toggle';
    // If no fresh events, label as "recent items" (archived from last daily)
    if (!hasFreshEvents) {
      title.textContent = `⚡ ${t('recentItems')} ${t('archived')}`;
    } else {
      title.textContent = `⚡ ${t('weekly')} ${t('signals')}`;
    }
    layer.appendChild(title);

    const body = document.createElement('div');
    body.className = 'collapsible-body';

    n.keyEvents.forEach(ev => {
      // For archived items (no fresh events), fall back to English to avoid empty cards
      let evTitle = getLangText(ev.title, '');
      if (!evTitle && !hasFreshEvents) {
        evTitle = typeof ev.title === 'string' ? ev.title : (ev.title.en || '');
      }
      if (!evTitle) return; // Skip events with no translation
      const attCount = ev.attestations ? ev.attestations.length : 0;
      body.innerHTML += `
        <div class="lc-event-item">
          ${typeBadge(ev.type)}
          <span class="lc-signal">${ev.signal_score || ev.effective_signal || '?'}</span>
          ${ev.link ? `<a href="${ev.link}" target="_blank" rel="noopener" class="lc-event-title">${evTitle}</a>` : `<span class="lc-event-title">${evTitle}</span>`}
          <span class="lc-source">${ev.source}</span>
          ${attCount ? `<span class="lc-attestations" title="${t('attestations')}">${attCount} ${t('sources')}</span>` : ''}
          ${ev.cross_attestation_bonus ? `<span class="lc-cross-attest" title="${t('crossAttestation')}">✦</span>` : ''}
        </div>
      `;
    });
    // Only append layer if it has at least one event
    if (body.innerHTML.trim()) {
      layer.appendChild(body);
      wrapper.appendChild(layer);
    }
  }

  // Key Perspectives layer (collapsible)
  if (n.keyOpinions && n.keyOpinions.length) {
    const layer = document.createElement('div');
    layer.className = 'lc-layer lc-layer-opinions';
    const title = document.createElement('div');
    title.className = 'lc-layer-title collapsible-toggle';
    title.textContent = `💭 ${t('opinions')}`;
    layer.appendChild(title);

    const body = document.createElement('div');
    body.className = 'collapsible-body';
    n.keyOpinions.forEach(ev => {
      const quote = getLangText(ev.quote, '');
      if (!quote) return; // Skip opinions with no translation
      body.innerHTML += `
        <div class="lc-event-item">
          ${typeBadge('opinion')}
          <span class="lc-signal">${ev.signal_score || ev.effective_signal || '?'}</span>
          ${ev.link ? `<a href="${ev.link}" target="_blank" rel="noopener" class="lc-event-title">"${quote}"</a>` : `<span class="lc-event-title">"${quote}"</span>`}
          <span class="lc-source">${ev.source}</span>
        </div>
      `;
    });
    // Only append layer if it has at least one opinion
    if (body.innerHTML.trim()) {
      layer.appendChild(body);
      wrapper.appendChild(layer);
    }
  }

  return wrapper;
}

function createLegacyCardTop(solution) {
  const top = document.createElement('div');
  top.className = 'card-top';
  const kv = solution.keyMetric || {};
  const n = solution.narrative || {};
  const eventsCount = (solution.events || []).length;
  const archivedItems = (n.keyEvents || []).length + (n.keyOpinions || []).length;
  let valHtml = eventsCount ? `${eventsCount}` : (archivedItems ? `${archivedItems} ${t('archived')}` : `${kv.value || '—'}`);
  if (kv.total && !eventsCount && !archivedItems) valHtml += ` / ${kv.total}`;
  const solutionName = getLangText(solution.name);
  const dirLabel = getDirectionLabel(solution.direction);
  top.innerHTML = `
    <span class="card-icon">${solution.icon}</span>
    <span class="card-name">${solutionName}</span>
    <span class="card-metric"><span class="card-metric-value">${valHtml}</span><span class="card-metric-label">${getLangText(kv.label, kv.label || '')}</span></span>
    <span class="card-direction ${solution.direction}">${dirLabel}</span>
  `;
  return top;
}

function buildLegacyEvents(solution) {
  const events = (solution.events || []).slice().sort((a, b) => {
    const da = parseDate(a.date) || new Date(0);
    const db = parseDate(b.date) || new Date(0);
    return db - da;
  });

  if (!events.length) return null;
  const evDiv = document.createElement('div');
  evDiv.className = 'card-events collapsible-body';
  const SENTIMENT_KEYS = { positive: 'sentimentPositive', neutral: 'sentimentNeutral', negative: 'sentimentNegative' };

  events.forEach(ev => {
    const text = getLangText(ev.text);
    if (!text) return; // Skip events with no translation for current language
    const src = ev.source ? ` <span class="card-event-source">(${ev.source})</span>` : '';
    const sentKey = ev.sentiment ? (SENTIMENT_KEYS[ev.sentiment] || ev.sentiment) : '';
    const sentLabel = sentKey ? t(sentKey) : '';
    const item = document.createElement('div');
    item.className = 'card-event';
    item.innerHTML = `
      <span class="card-event-dot sentiment-${ev.sentiment || 'neutral'}"></span>
      ${sentLabel ? `<span class="card-event-sentiment sentiment-${ev.sentiment}">${sentLabel}</span>` : ''}
      <span class="card-event-time">${formatTime(ev.date)}</span>
      ${ev.link ? `<a href="${ev.link}" target="_blank" rel="noopener" class="card-event-text">${text}</a>` : `<span class="card-event-text">${text}</span>`}
      ${src}
    `;
    evDiv.appendChild(item);
  });
  return evDiv;
}

function buildStakeholders(stakeholders) {
  const div = document.createElement('div');
  div.className = 'card-players';
  const title = document.createElement('div');
  title.className = 'card-players-title';
  title.textContent = t('keyPlayers');
  div.appendChild(title);

  const row = document.createElement('div');
  row.className = 'card-players-row';
  stakeholders.forEach((p, i) => {
    if (i > 0) {
      const sep = document.createElement('span');
      sep.className = 'card-players-sep';
      sep.textContent = ',';
      row.appendChild(sep);
    }
    const chip = document.createElement('span');
    chip.className = 'card-player-chip';
    if (p.contact) {
      const link = document.createElement('a');
      link.className = 'card-player-link';
      link.href = p.contact;
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = p.name;
      link.title = `${p.name} — ${p.role}`;
      chip.appendChild(link);
    } else {
      chip.textContent = p.name;
      chip.title = `${p.name} — ${p.role}`;
    }
    if (p.email) {
      const em = document.createElement('a');
      em.className = 'card-player-email';
      em.href = `mailto:${p.email}`;
      em.textContent = '✉';
      em.title = `Email: ${p.email}`;
      chip.appendChild(em);
    }
    row.appendChild(chip);
  });
  div.appendChild(row);
  return div;
}

/* ── Render All ──────────────────────────────────────── */
function renderAll(data) {
  renderMomentum(data.overallMomentum);
  renderClassificationWarning(data.aiHealth);

  if (data.lastUpdated) {
    const ts = document.getElementById('lastUpdated');
    ts.textContent = `${t('lastUpdated')} ${formatTime(data.lastUpdated)}`;
  }

  const vt = document.getElementById('versionTag');
  if (vt) {
    const appVersion = 'v0.5.10';
    const aiVersion = data.aiVersion ? ` AI ${data.aiVersion}` : '';
    vt.textContent = `${appVersion}${aiVersion}`;
  }

  buildActivityFeed();

  const grid = document.getElementById('solutionsGrid');
  if (grid) grid.innerHTML = '';
  const activeIds = data.activeSolutions || data.solutions.map(s => s.id);

  // Helper: check if a solution has any displayable content
  function hasContent(sol) {
    if ((sol.events || []).length > 0) return true;
    const n = sol.narrative || {};
    if ((n.keyEvents || []).length > 0) return true;
    if ((n.keyOpinions || []).length > 0) return true;
    const lt = getLangText(n.longTerm, '');
    if (lt) return true;
    const wh = getLangText(n.weeklyHighlight, '');
    if (wh) return true;
    return false;
  }

  window.__debug_filter = [];
  (data.solutions || [])
    .filter(solution => {
      const inActive = activeIds.includes(solution.id);
      const hc = hasContent(solution);
      const pass = inActive && hc;
      window.__debug_filter.push({id: solution.id, inActive: inActive, hasContent: hc, pass: pass, events: (solution.events||[]).length, keyEvents: (solution.narrative||{}).keyEvents ? (solution.narrative.keyEvents||[]).length : 'NO NARRATIVE'});
      return pass;
    })
    .sort((a, b) => {
      // Sort by effective_signal total if available, else by event count
      const aTotal = (a.narrative?.keyEvents || []).reduce((s, e) => s + (e.effective_signal || e.signal_score || 0), 0) || parseInt(a.keyMetric?.value || 0);
      const bTotal = (b.narrative?.keyEvents || []).reduce((s, e) => s + (e.effective_signal || e.signal_score || 0), 0) || parseInt(b.keyMetric?.value || 0);
      return bTotal - aTotal;
    })
    .slice(0, 8)
    .forEach(solution => {
      const card = createSolutionCard(solution);
      if (grid) grid.appendChild(card);
    });

  // Re-init collapsible toggles after DOM rebuild
  initCollapsibles();
}

/* ── Language Switcher ───────────────────────────────── */
function initLanguageSwitcher() {
  const container = document.getElementById('langSwitcher');
  if (!container) return;

  const langs = [
    { code: 'en', label: 'English', dir: 'ltr' },
    { code: 'he', label: 'עברית', dir: 'rtl' },
    { code: 'ar', label: 'العربية', dir: 'rtl' },
  ];

  container.innerHTML = '';
  langs.forEach(l => {
    const btn = document.createElement('button');
    btn.className = 'lang-btn';
    btn.dataset.lang = l.code;
    btn.textContent = l.label;
    btn.title = `${l.label} (${l.dir})`;
    btn.addEventListener('click', () => applyLanguage(l.code));
    container.appendChild(btn);
  });
}

/* ── Info Modal ──────────────────────────────────────── */
function renderInfoModal() {
  const content = document.getElementById('modalContent');
  content.innerHTML = `
    <h2>${t('infoTitle')}</h2>

    <h3>${t('infoDataCollection')}</h3>
    <p>${t('infoDataCollectionText')}</p>

    <h3>${t('infoAI')}</h3>
    <p>${t('infoAIText')}</p>
    <ul>
      <li>${t('infoClassifies')}</li>
      <li>${t('infoType')}</li>
      <li>${t('infoSignal')}</li>
      <li>${t('infoSourceWeight')}</li>
    </ul>
    <p>${t('infoEffectiveSignal')}</p>

    <h3>${t('infoClustering')}</h3>
    <p>${t('infoClusteringText')}</p>

    <h3>${t('infoNarrative')}</h3>
    <p>${t('infoNarrativeText')}</p>
    <ul>
      <li>${t('infoNarrativeLongTerm')}</li>
      <li>${t('infoNarrativeWeekly')}</li>
      <li>${t('infoNarrativeOpinions')}</li>
    </ul>

    <h3>${t('infoMultilingual')}</h3>
    <p>${t('infoMultilingualText')}</p>

    <h3>${t('infoLimitations')}</h3>
    <ul>
      <li>${t('infoLimit1')}</li>
      <li>${t('infoLimit2')}</li>
      <li>${t('infoLimit3')}</li>
    </ul>
  `;
}

document.getElementById('infoBtn')?.addEventListener('click', (e) => {
  e.preventDefault();
  const overlay = document.getElementById('modalOverlay');
  renderInfoModal();
  overlay.classList.add('active');
});

document.getElementById('modalClose')?.addEventListener('click', () => {
  document.getElementById('modalOverlay').classList.remove('active');
});

document.getElementById('modalOverlay')?.addEventListener('click', (e) => {
  if (e.target === e.currentTarget) {
    document.getElementById('modalOverlay').classList.remove('active');
  }
});

/* ── Theme Toggle ────────────────────────────────────── */
let currentTheme = localStorage.getItem('theme') || 'dark';

function applyTheme(theme) {
  currentTheme = theme;
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('theme', theme);
  const btn = document.getElementById('themeToggle');
  if (btn) {
    btn.textContent = theme === 'dark' ? '☀️' : '🌙';
    btn.title = theme === 'dark' ? t('lightMode') : t('darkMode');
  }
}

/* ── Collapsible Sections ──────────────────────────── */
function initCollapsibles() {
  document.querySelectorAll('.collapsible-toggle').forEach(toggle => {
    toggle.addEventListener('click', () => {
      // Find the next sibling that is the collapsible body
      let next = toggle.nextElementSibling;
      if (!next || !next.classList.contains('collapsible-body')) {
        // Try finding within the same parent
        const parent = toggle.parentElement;
        if (parent) {
          for (const child of parent.children) {
            if (child === toggle) {
              next = child.nextElementSibling;
              break;
            }
          }
        }
      }
      if (next && next.classList.contains('collapsible-body')) {
        const isOpen = next.classList.contains('open');
        next.classList.toggle('open', !isOpen);
        toggle.classList.toggle('open', !isOpen);
      }
    });
  });
}

/* ── Boot ────────────────────────────────────────────── */
(async function boot() {
  await loadTranslations();
  applyTheme(currentTheme);
  initLanguageSwitcher();
  applyLanguage(detectLanguage());
  const themeToggle = document.getElementById('themeToggle');
  if (themeToggle) {
    themeToggle.addEventListener('click', () => {
      applyTheme(currentTheme === 'dark' ? 'light' : 'dark');
    });
  }
  loadData();
})();

// Auto-refresh