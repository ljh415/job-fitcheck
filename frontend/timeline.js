/* ── 타임라인 ─────────────────────────────────────────────────────── */
let _timelineData = [];
let _tlCurrentTab = 'list';
let _calYear = new Date().getFullYear();
let _calMonth = new Date().getMonth(); // 0-indexed

const _STATUS_BASE_COLORS = {
  '지원': '#3b82f6',
  '서류통과': '#10b981',
  '인터뷰': '#f59e0b',
  '최종': '#22c55e',
  '탈락': '#ef4444',
  '보류': '#a78bfa',
  '지원마감': '#6b7280',
};
const STATUS_COLOR = { '미지원': '#9ca3af', ..._STATUS_BASE_COLORS };
const LOG_LABEL_COLOR = { '분석 완료': '#9ca3af', '등록': '#9ca3af', ..._STATUS_BASE_COLORS };

function _logColor(label) {
  for (const [key, color] of Object.entries(LOG_LABEL_COLOR)) {
    if (label.includes(key)) return color;
  }
  return '#9ca3af';
}

const TL_SUMMARY_GROUPS = [
  { label: '지원 중',   statuses: ['지원'],       color: '#3b82f6' },
  { label: '서류통과',  statuses: ['서류통과'],    color: '#10b981' },
  { label: '면접',      statuses: ['인터뷰'],      color: '#f59e0b' },
  { label: '최종',      statuses: ['최종'],        color: '#22c55e' },
  { label: '탈락',      statuses: ['탈락'],        color: '#ef4444' },
  { label: '보류',      statuses: ['보류'],        color: '#a78bfa' },
];

function renderTimelineSummary() {
  const el = document.getElementById('tl-summary');
  if (!el) return;
  const counts = {};
  for (const c of _timelineData) counts[c.status] = (counts[c.status] || 0) + 1;
  el.innerHTML = TL_SUMMARY_GROUPS
    .map(g => {
      const n = g.statuses.reduce((s, st) => s + (counts[st] || 0), 0);
      if (n === 0) return '';
      return `<div class="tl-summary-card" style="border-color:${g.color}">
        <span class="tl-summary-count" style="color:${g.color}">${n}</span>
        <span class="tl-summary-label">${g.label}</span>
      </div>`;
    })
    .join('');
}

async function initTimeline() {
  try {
    _timelineData = await api('/companies/timeline');
  } catch (e) {
    showToast('타임라인 로드 실패: ' + e.message, 'error');
    _timelineData = [];
  }
  _tlCurrentTab = 'list';
  _calYear = new Date().getFullYear();
  _calMonth = new Date().getMonth();
  renderTimelineSummary();
  renderTimelineList();
}

function switchTimelineTab(tab) {
  _tlCurrentTab = tab;
  document.getElementById('tl-tab-list')?.classList.toggle('active', tab === 'list');
  document.getElementById('tl-tab-cal')?.classList.toggle('active', tab === 'cal');
  document.getElementById('tl-list-panel')?.classList.toggle('hidden', tab !== 'list');
  document.getElementById('tl-cal-panel')?.classList.toggle('hidden', tab !== 'cal');
  if (tab === 'cal') renderCalendar();
}

const EXCLUDED_LOG_LABELS = new Set(['분석 완료', '재분석 완료', '등록', '적합도 재평가 완료']);
const ACTIVE_STATUSES = new Set(['지원', '서류통과', '인터뷰', '최종', '보류']);
const CLOSED_STATUSES = new Set(['탈락', '지원마감']);
let _tlShowClosed = false;

function _isAppliedEntry(label) {
  return !EXCLUDED_LOG_LABELS.has(label);
}

function _tlVisibleStatuses() {
  return _tlShowClosed
    ? new Set([...ACTIVE_STATUSES, ...CLOSED_STATUSES])
    : ACTIVE_STATUSES;
}

function toggleTimelineClosed() {
  _tlShowClosed = !_tlShowClosed;
  const btn = document.getElementById('tl-show-closed-btn');
  if (btn) btn.textContent = _tlShowClosed ? '종료 숨기기' : '종료 보기';
  if (_tlCurrentTab === 'list') renderTimelineList();
  else renderCalendar();
}

function _buildFilteredEntries() {
  const entries = [];
  const seen = new Set();
  const visibleStatuses = _tlVisibleStatuses();
  for (const c of _timelineData) {
    if (!visibleStatuses.has(c.status)) continue;
    for (const e of c.log_entries) {
      if (!_isAppliedEntry(e.label)) continue;
      const key = `${e.date}__${c.slug}`;
      if (seen.has(key)) continue;
      seen.add(key);
      entries.push({ ...e, company: c });
    }
  }
  entries.sort((a, b) => b.date.localeCompare(a.date));
  return entries;
}

function renderTimelineList() {
  const container = document.getElementById('tl-list-body');
  if (!container) return;

  const entries = _buildFilteredEntries();

  if (entries.length === 0) {
    container.innerHTML = '<p class="empty">지원한 회사가 없습니다. 대시보드에서 상태를 "지원"으로 변경해보세요.</p>';
    return;
  }

  // Group by month
  const byMonth = {};
  for (const e of entries) {
    const monthKey = e.date.slice(0, 7); // YYYY-MM
    if (!byMonth[monthKey]) byMonth[monthKey] = [];
    byMonth[monthKey].push(e);
  }

  let html = '';
  for (const monthKey of Object.keys(byMonth).sort().reverse()) {
    const [y, m] = monthKey.split('-');
    html += `<div class="tl-month-group">
      <div class="tl-month-label">${y}년 ${parseInt(m)}월</div>`;
    for (const e of byMonth[monthKey]) {
      const color = _logColor(e.label);
      const score = e.company.fit_score != null ? `<span class="tl-score">${e.company.fit_score}점</span>` : '';
      html += `<div class="tl-entry" data-slug="${escHtml(e.company.slug)}" data-nav="detail">
        <div class="tl-dot" style="background:${color}"></div>
        <div class="tl-entry-meta">
          <span class="tl-date">${e.date.slice(5)}</span>
          <span class="tl-label" style="color:${color}">${escHtml(e.label)}</span>
        </div>
        <div class="tl-entry-info">
          <span class="tl-company">${escHtml(e.company.display_name)}</span>
          <span class="tl-job">${escHtml(e.company.job_title)}</span>
        </div>
        <div class="tl-entry-badges">
          ${score}
          <span class="tl-status-badge" style="background:${STATUS_COLOR[e.company.status] || '#9ca3af'}">${escHtml(e.company.status)}</span>
        </div>
      </div>`;
    }
    html += '</div>';
  }
  container.innerHTML = html;
}

function renderCalendar() {
  const label = document.getElementById('cal-month-label');
  const grid = document.getElementById('cal-grid');
  if (!label || !grid) return;

  label.textContent = `${_calYear}년 ${_calMonth + 1}월`;

  const dateMap = {};
  for (const e of _buildFilteredEntries()) {
    const [y, m] = e.date.split('-').map(Number);
    if (y === _calYear && m - 1 === _calMonth) {
      if (!dateMap[e.date]) dateMap[e.date] = [];
      dateMap[e.date].push(e);
    }
  }

  // First day of month weekday (0=Sun)
  const firstDay = new Date(_calYear, _calMonth, 1).getDay();
  const daysInMonth = new Date(_calYear, _calMonth + 1, 0).getDate();
  const today = localDateString();

  let html = '';
  // Leading empty cells
  for (let i = 0; i < firstDay; i++) html += '<div class="cal-cell cal-cell--empty"></div>';

  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${_calYear}-${String(_calMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    const isToday = dateStr === today;
    const dayEntries = dateMap[dateStr] || [];
    const chips = dayEntries.slice(0, 3).map(e => {
      const color = _logColor(e.label);
      return `<div class="cal-chip" style="background:${color}" data-slug="${escHtml(e.company.slug)}" data-nav="detail" title="${escHtml(e.company.display_name)} — ${escHtml(e.company.job_title)} (${escHtml(e.label)})"><div class="cal-chip-name">${escHtml(e.company.display_name)}</div><div class="cal-chip-job">${escHtml(e.company.job_title)}</div></div>`;
    }).join('');
    const more = dayEntries.length > 3 ? `<div class="cal-chip-more">+${dayEntries.length - 3}</div>` : '';
    html += `<div class="cal-cell${isToday ? ' cal-cell--today' : ''}">
      <span class="cal-day-num">${d}</span>
      <div class="cal-chips">${chips}${more}</div>
    </div>`;
  }

  grid.innerHTML = html;
}

function calPrevMonth() {
  _calMonth--;
  if (_calMonth < 0) { _calMonth = 11; _calYear--; }
  renderCalendar();
}

function calNextMonth() {
  _calMonth++;
  if (_calMonth > 11) { _calMonth = 0; _calYear++; }
  renderCalendar();
}
