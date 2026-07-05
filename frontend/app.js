/* ── marked 확장: ==텍스트== → <mark> 하이라이트 ───────────────────── */
function parseMarkdown(text) {
  let html = marked.parse(text || '');
  html = html.replace(/==([^=\n]+)==/g, '<mark class="mark-pos">$1</mark>');
  html = html.replace(/!!([^!\n]+)!!/g, '<mark class="mark-neg">$1</mark>');
  return DOMPurify.sanitize(html, { ADD_ATTR: ['class'] });
}

/* ── 테마 ─────────────────────────────────────────────────────────── */
function applyHljs(container) {
  if (typeof hljs === 'undefined') return;
  container.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
}

function setHljsTheme(dark) {
  document.getElementById('hljs-light').disabled = dark;
  document.getElementById('hljs-dark').disabled = !dark;
}

(function initTheme() {
  const saved = localStorage.getItem('job-fitcheck-theme');
  if (saved === 'dark') {
    document.documentElement.setAttribute('data-theme', 'dark');
    document.getElementById('theme-toggle-btn').textContent = '☀️';
    setHljsTheme(true);
  }
})();

function toggleTheme() {
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  const btn = document.getElementById('theme-toggle-btn');
  if (isDark) {
    document.documentElement.removeAttribute('data-theme');
    btn.textContent = '🌙';
    localStorage.setItem('job-fitcheck-theme', 'light');
    setHljsTheme(false);
  } else {
    document.documentElement.setAttribute('data-theme', 'dark');
    btn.textContent = '☀️';
    localStorage.setItem('job-fitcheck-theme', 'dark');
    setHljsTheme(true);
  }
}

/* ── 상태 ─────────────────────────────────────────────────────────── */
const TOKEN_KEY = 'job-fitcheck-token';
let currentView = 'dashboard';
let currentSlug = null;
let allCompanies = [];
let selectedSlugs = new Set();
let compareTargets = [];
const TERMINATED = new Set(['탈락', '지원마감']);
let hideTerminated = localStorage.getItem('hide-terminated') !== 'false'; // 기본: 숨김
let filterPinnedOnly = false;
let _activeSSEReader = null;
let _audioCtx = null;
let _currentRecord = null;
const qaHistory = JSON.parse(localStorage.getItem('job-fitcheck-qa') || '{}');
function saveQAHistory() {
  try { localStorage.setItem('job-fitcheck-qa', JSON.stringify(qaHistory)); }
  catch (e) { console.warn('QA 히스토리 저장 실패(용량 초과):', e); }
}

function escHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function safeHref(url) {
  try { const u = new URL(url); return (u.protocol === 'http:' || u.protocol === 'https:') ? url : '#'; }
  catch { return '#'; }
}

/* ── 라우터 ───────────────────────────────────────────────────────── */
function viewToUrl(view, slug) {
  if (view === 'detail' && slug) return `/detail/${encodeURIComponent(slug)}`;
  if (view === 'add') return '/add';
  if (view === 'compare') return '/compare';
  if (view === 'settings') return '/settings';
  if (view === 'timeline') return '/timeline';
  return '/';
}

function parseUrl() {
  const path = window.location.pathname;
  const detailMatch = path.match(/^\/detail\/(.+)$/);
  if (detailMatch) return { view: 'detail', slug: decodeURIComponent(detailMatch[1]) };
  if (path === '/add') return { view: 'add', slug: null };
  if (path === '/compare') return { view: 'compare', slug: null };
  if (path === '/settings') return { view: 'settings', slug: null };
  if (path === '/timeline') return { view: 'timeline', slug: null };
  return { view: 'dashboard', slug: null };
}

function navigate(view, slug = null, replace = false) {
  if (_activeSSEReader) { _activeSSEReader.cancel(); _activeSSEReader = null; }
  currentView = view;
  currentSlug = slug;
  selectedSlugs.clear();
  const url = viewToUrl(view, slug);
  const state = { view, slug, compareTargets: compareTargets.slice() };
  if (replace) history.replaceState(state, '', url);
  else history.pushState(state, '', url);
  render();
}

window.addEventListener('popstate', (e) => {
  const state = e.state;
  if (!state) return;
  if (_activeSSEReader) { _activeSSEReader.cancel(); _activeSSEReader = null; }
  currentView = state.view || 'dashboard';
  currentSlug = state.slug || null;
  if (Array.isArray(state.compareTargets)) compareTargets = state.compareTargets;
  selectedSlugs.clear();
  render();
});

function render() {
  const app = document.getElementById('app');
  const tpl = document.getElementById(`tpl-${currentView}`);
  app.innerHTML = tpl.innerHTML;

  const logoutBtn = document.getElementById('logout-btn');
  if (logoutBtn) {
    if (currentView === 'login') logoutBtn.classList.add('hidden');
    else logoutBtn.classList.remove('hidden');
  }

  if (currentView === 'login') initLogin();
  else if (currentView === 'dashboard') initDashboard();
  else if (currentView === 'detail') initDetail(currentSlug);
  else if (currentView === 'add') initAdd();
  else if (currentView === 'compare') initCompare(compareTargets);
  else if (currentView === 'settings') initSettings();
  else if (currentView === 'timeline') initTimeline();
}

/* ── 공통 API ─────────────────────────────────────────────────────── */
async function api(path, opts = {}) {
  const token = localStorage.getItem(TOKEN_KEY);
  const res = await fetch('/api' + path, {
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    },
    ...opts,
  });
  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    navigate('login', null, true);
    throw new Error('인증이 필요합니다.');
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || '요청 실패');
  }
  return res.json();
}

/* ── 로그인 ───────────────────────────────────────────────────────── */
function initLogin() {
  document.getElementById('login-password')?.focus();
}

async function submitLogin(event) {
  event.preventDefault();
  const password = document.getElementById('login-password').value;
  const errorEl = document.getElementById('login-error');
  try {
    const { token } = await api('/login', {
      method: 'POST',
      body: JSON.stringify({ password }),
    });
    localStorage.setItem(TOKEN_KEY, token);
    navigate('dashboard');
  } catch (e) {
    errorEl.classList.remove('hidden');
  }
}

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  navigate('login', null, true);
}

/* ── 대시보드 ─────────────────────────────────────────────────────── */
async function initDashboard() {
  try {
    const [health, companies] = await Promise.all([
      api('/health'),
      api('/companies'),
    ]);
    allCompanies = companies;

    if (!health.profile_exists) {
      document.getElementById('profile-banner').classList.remove('hidden');
    }

    applyFilters();
  } catch (e) {
    showToast('대시보드 로딩 실패: ' + e.message, 'error');
  }
}

function renderTable(companies, terminatedCount = 0) {
  renderPinnedSection(allCompanies.filter(c => c.frontmatter.pinned));
  renderMainTable(companies, terminatedCount);
}

function renderPinnedSection(pinnedCompanies) {
  const section = document.getElementById('pinned-section');
  const cards = document.getElementById('pinned-cards');
  const countEl = document.getElementById('pinned-count');
  if (!section || !cards) return;

  if (!pinnedCompanies.length) {
    section.classList.add('hidden');
    return;
  }
  section.classList.remove('hidden');
  if (countEl) countEl.textContent = pinnedCompanies.length;

  cards.innerHTML = pinnedCompanies.map(c => {
    const fm = c.frontmatter;
    const score = fm.fit_score;
    const scoreClass = score == null ? 'score-none' : score >= 70 ? 'score-high' : score >= 50 ? 'score-mid' : 'score-low';
    const scoreText = score != null ? score : '-';
    const slug = escHtml(c.slug);
    return `<div class="pinned-card" onclick="navigate('detail','${slug}')">
      <button class="pin-card-btn" onclick="event.stopPropagation();togglePin('${slug}')" title="핀 해제">📌</button>
      <div class="pinned-card-name">${escHtml(fm.company_name)}</div>
      <div class="pinned-card-job">${escHtml(fm.job_title || '-')}</div>
      <div class="pinned-card-meta">
        <span class="score-badge ${scoreClass}">${scoreText}</span>
        <span class="status-chip status-${escHtml(fm.status)}">${escHtml(fm.status)}</span>
      </div>
    </div>`;
  }).join('');
}

function renderMainTable(companies, terminatedCount = 0) {
  const tbody = document.getElementById('company-tbody');
  const empty = document.getElementById('empty-msg');
  const activeCount = companies.length - terminatedCount;

  if (!companies.length) {
    tbody.innerHTML = '';
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');

  const rows = companies.map((c, i) => {
    const fm = c.frontmatter;
    const isTerminated = i >= activeCount;
    const score = fm.fit_score;
    const scoreClass = score === null || score === undefined ? 'score-none'
      : score >= 70 ? 'score-high'
      : score >= 50 ? 'score-mid'
      : 'score-low';
    const scoreText = score !== null && score !== undefined ? score : '-';
    const slug = escHtml(c.slug);
    const status = escHtml(fm.status);
    const isPinned = !!fm.pinned;
    return `<tr class="company-row${isTerminated ? ' terminated-row' : ''}" onclick="navigate('detail','${slug}')">
      <td onclick="event.stopPropagation()"><input type="checkbox" class="row-check" data-slug="${slug}" onchange="onCheckChange()" /></td>
      <td onclick="event.stopPropagation()">
        <button class="pin-btn${isPinned ? ' active' : ''}" onclick="togglePin('${slug}')" title="${isPinned ? '핀 해제' : '즐겨찾기 추가'}">📌</button>
      </td>
      <td>
        <strong>${escHtml(fm.company_name)}</strong>
        ${fm.source_url ? `<br/><a href="${safeHref(fm.source_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()" style="font-size:11px;color:#4361ee;font-weight:600;">🔗 공고 원문</a>` : ''}
      </td>
      <td>${escHtml(fm.job_title || '-')}</td>
      <td><span class="score-badge ${scoreClass}">${scoreText}</span></td>
      <td>${escHtml(fm.fit_label || '-')}</td>
      <td>${escHtml(fm.stability || '-')}</td>
      <td>${escHtml(fm.location || '-')}</td>
      <td onclick="event.stopPropagation()">
        <select class="status-select status-${status}" onchange="onStatusChange('${slug}', this)">
          ${['미지원','지원','서류통과','인터뷰','최종','탈락','보류','지원마감'].map(s =>
            `<option value="${s}" ${fm.status === s ? 'selected' : ''}>${s}</option>`
          ).join('')}
        </select>
      </td>
      <td>${(fm.updated_at || '').slice(0, 10)}</td>
    </tr>`;
  });

  if (terminatedCount > 0 && activeCount > 0) {
    rows.splice(activeCount, 0,
      `<tr class="terminated-separator"><td colspan="10">── 탈락 · 지원마감 ──</td></tr>`
    );
  }

  tbody.innerHTML = rows.join('');
}

async function togglePin(slug) {
  try {
    const result = await api(`/companies/${slug}/pin`, { method: 'POST', body: '{}' });
    const company = allCompanies.find(c => c.slug === slug);
    if (company) company.frontmatter.pinned = result.pinned;
    applyFilters();
  } catch (e) {
    showToast('즐겨찾기 변경 실패: ' + e.message, 'error');
  }
}

function toggleHideTerminated() {
  hideTerminated = !hideTerminated;
  localStorage.setItem('hide-terminated', String(hideTerminated));
  applyFilters();
}

function toggleFilterPinned() {
  filterPinnedOnly = !filterPinnedOnly;
  applyFilters();
}

function applyFilters() {
  const q = (document.getElementById('search')?.value || '').toLowerCase();
  const statusFilter = document.getElementById('filter-status')?.value || '';
  const scoreFilter = document.getElementById('filter-score')?.value || '';
  const sortBy = document.getElementById('sort-by')?.value || 'updated';

  // 버튼 레이블 동기화
  const hideBtn = document.getElementById('hide-terminated-btn');
  if (hideBtn) hideBtn.textContent = hideTerminated ? '탈락 보기' : '탈락 숨기기';

  // 핀 헤더 아이콘 동기화
  const pinHeader = document.getElementById('pin-header-btn');
  if (pinHeader) pinHeader.style.opacity = filterPinnedOnly ? '1' : '0.3';

  let result = allCompanies.filter(c => {
    const fm = c.frontmatter;

    if (filterPinnedOnly && !fm.pinned) return false;

    const matchSearch = !q || [fm.company_name, fm.display_name, fm.job_title, fm.location, fm.status]
      .some(v => (v || '').toLowerCase().includes(q));

    const matchStatus = !statusFilter || fm.status === statusFilter;

    const score = fm.fit_score;
    let matchScore = true;
    if (scoreFilter === 'high') matchScore = score != null && score >= 70;
    else if (scoreFilter === 'mid') matchScore = score != null && score >= 50 && score < 70;
    else if (scoreFilter === 'low') matchScore = score != null && score < 50;
    else if (scoreFilter === 'none') matchScore = score == null;

    return matchSearch && matchStatus && matchScore;
  });

  const sortFn = (a, b) => {
    if (sortBy === 'score') return (b.frontmatter.fit_score || 0) - (a.frontmatter.fit_score || 0);
    if (sortBy === 'status') return (a.frontmatter.status || '').localeCompare(b.frontmatter.status || '');
    return (b.frontmatter.updated_at || '').localeCompare(a.frontmatter.updated_at || '');
  };

  // 상태 필터가 탈락/지원마감 자체를 지목한 경우엔 hide 로직 건너뜀
  const filterTargetsTerminated = TERMINATED.has(statusFilter);

  if (hideTerminated && !filterTargetsTerminated) {
    result = result.filter(c => !TERMINATED.has(c.frontmatter.status));
    result.sort(sortFn);
    renderTable(result);
  } else {
    const active = result.filter(c => !TERMINATED.has(c.frontmatter.status));
    const terminated = result.filter(c => TERMINATED.has(c.frontmatter.status));
    active.sort(sortFn);
    terminated.sort(sortFn);
    renderTable([...active, ...terminated], filterTargetsTerminated ? 0 : terminated.length);
  }
}

function filterTable() { applyFilters(); }
function sortTable() { applyFilters(); }

function onCheckChange() {
  selectedSlugs = new Set(
    [...document.querySelectorAll('.row-check:checked')].map(el => el.dataset.slug)
  );
  const compareBtn = document.getElementById('compare-btn');
  if (compareBtn) {
    if (selectedSlugs.size >= 2) compareBtn.classList.remove('hidden');
    else compareBtn.classList.add('hidden');
  }
  const deleteBtn = document.getElementById('delete-btn');
  if (deleteBtn) {
    if (selectedSlugs.size >= 1) deleteBtn.classList.remove('hidden');
    else deleteBtn.classList.add('hidden');
  }
}

async function deleteSelected() {
  if (!confirm(`선택한 ${selectedSlugs.size}개 회사를 삭제하시겠습니까?`)) return;
  const results = await Promise.allSettled(
    [...selectedSlugs].map(slug => api(`/companies/${slug}`, { method: 'DELETE' }))
  );
  const failed = results.filter(r => r.status === 'rejected').length;
  if (failed > 0) showToast(`${failed}개 삭제 실패`, 'error');
  selectedSlugs.clear();
  navigate('dashboard');
}

function toggleAll(el) {
  document.querySelectorAll('.row-check').forEach(c => { c.checked = el.checked; });
  onCheckChange();
}

async function onStatusChange(slug, selectEl) {
  const newStatus = selectEl.value;
  const prevClass = selectEl.className;
  const prevValue = [...selectEl.options].find(o => o.defaultSelected)?.value || selectEl.options[0]?.value;
  selectEl.className = `status-select status-${newStatus}`;
  let record;
  try {
    record = await api(`/companies/${slug}`);
  } catch (e) {
    selectEl.className = prevClass;
    selectEl.value = prevValue;
    showToast('상태 변경 실패: ' + e.message, 'error');
    return;
  }
  record.frontmatter.status = newStatus;

  // 지원 상태 로그 섹션에 날짜 자동 기록
  const today = new Date().toISOString().slice(0, 10);
  const logEntry = `- ${today}: ${newStatus}`;
  const logPattern = /(##\s*\d*\.?\s*지원 상태 로그)/;
  if (logPattern.test(record.body)) {
    record.body = record.body.replace(logPattern, `$1\n${logEntry}`);
  } else {
    record.body = (record.body || '') + `\n\n## 지원 상태 로그\n${logEntry}`;
  }

  try {
    await api(`/companies/${encodeURIComponent(slug)}`, {
      method: 'PUT',
      body: JSON.stringify({ frontmatter: record.frontmatter, body: record.body }),
    });
    const company = allCompanies.find(c => c.slug === slug);
    if (company) company.frontmatter.status = newStatus;

    const AUTO_PIN_ON  = new Set(['지원']);
    const AUTO_UNPIN_ON = new Set(['미지원', '탈락', '보류', '지원마감']);

    if (AUTO_PIN_ON.has(newStatus) && !record.frontmatter.pinned) {
      const pinResult = await api(`/companies/${encodeURIComponent(slug)}/pin`, { method: 'POST', body: '{}' });
      if (company) company.frontmatter.pinned = pinResult.pinned;
      render();
    } else if (AUTO_UNPIN_ON.has(newStatus) && record.frontmatter.pinned) {
      const pinResult = await api(`/companies/${encodeURIComponent(slug)}/pin`, { method: 'POST', body: '{}' });
      if (company) company.frontmatter.pinned = pinResult.pinned;
      render();
    }
  } catch (e) {
    selectEl.className = prevClass;
    selectEl.value = record.frontmatter.status;
    showToast('상태 저장 실패: ' + e.message, 'error');
  }
}

function goCompare() {
  compareTargets = [...selectedSlugs];
  navigate('compare');
}

/* ── 상세 뷰 ─────────────────────────────────────────────────────── */
async function initDetail(slug) {
  let record;
  try {
    record = await api(`/companies/${slug}`);
  } catch (e) {
    showToast('회사 정보 로딩 실패: ' + e.message, 'error');
    navigate('dashboard');
    return;
  }
  _currentRecord = record;
  const fm = record.frontmatter;

  // 적합도 배지
  const score = fm.fit_score;
  const badgeClass = score === null || score === undefined ? 'score-none'
    : score >= 70 ? 'score-high'
    : score >= 50 ? 'score-mid'
    : 'score-low';
  const badgeEl = document.getElementById('fit-badge');
  if (badgeEl) {
    badgeEl.innerHTML = `<span class="score-badge ${badgeClass}" style="font-size:16px;padding:5px 14px;">
      ${score !== null && score !== undefined ? score + '점' : '미평가'} ${escHtml(fm.fit_label || '')}
    </span>`;
  }

  // 메타 칩
  const chips = document.getElementById('meta-chips');
  if (chips) {
    const items = [fm.stability && `안정성: ${escHtml(fm.stability)}`, escHtml(fm.location), escHtml(fm.employee_count)].filter(Boolean);
    const statusSel = escHtml(fm.status);
    chips.innerHTML = items.map(i => `<span class="chip">${i}</span>`).join('') +
      `<select class="status-select status-${statusSel}" onchange="onStatusChange('${escHtml(currentSlug)}', this)">
        ${['미지원','지원','서류통과','인터뷰','최종','탈락','보류','지원마감'].map(s =>
          `<option value="${s}" ${fm.status === s ? 'selected' : ''}>${s}</option>`
        ).join('')}
      </select>`;
  }

  // 기업 현황 행 (Wanted 동기화 데이터)
  const companyFacts = document.getElementById('company-facts');
  if (companyFacts) {
    const facts = [];
    if (fm.investment_stage) facts.push(`🏢 ${escHtml(fm.investment_stage)}`);
    if (fm.revenue_status) facts.push(`📈 매출 ${escHtml(fm.revenue_status)}`);
    if (fm.jobplanet_score) facts.push(`⭐ 잡플래닛 ${escHtml(String(fm.jobplanet_score))}`);
    if (fm.source_url) facts.push(`<a href="${safeHref(fm.source_url)}" target="_blank" rel="noopener" style="display:inline-block;padding:2px 10px;background:#e8f0fe;color:#4361ee;border-radius:12px;font-weight:600;text-decoration:none;">🔗 공고 원문</a>`);
    companyFacts.innerHTML = facts.join('<span class="facts-sep">·</span>');
    companyFacts.classList.toggle('hidden', facts.length === 0);
  }

  // 마크다운 본문
  const bodyEl = document.getElementById('company-body');
  if (bodyEl && record.body) {
    bodyEl.innerHTML = parseMarkdown(record.body);
    applyHljs(bodyEl);
    // 섹션 1·2 테이블 첫 번째 열 너비 고정
    for (const h2 of bodyEl.querySelectorAll('h2')) {
      const text = h2.textContent.trim();
      if (/^[12]\./.test(text)) {
        let el = h2.nextElementSibling;
        while (el && el.tagName !== 'TABLE' && el.tagName !== 'H2') el = el.nextElementSibling;
        if (el?.tagName === 'TABLE') el.classList.add('info-table');
      }
    }
    // 충족 현황 테이블 열 너비 고정
    for (const h3 of bodyEl.querySelectorAll('h3')) {
      if (h3.textContent.includes('충족 현황')) {
        let el = h3.nextElementSibling;
        while (el && el.tagName !== 'TABLE' && el.tagName !== 'H3') el = el.nextElementSibling;
        if (el?.tagName === 'TABLE') el.classList.add('fit-check-table');
      }
    }
  }

  // 편집 폼 채우기
  fillEditForm(fm, record.body);
}

function switchTab(name) {
  ['info', 'edit', 'qa'].forEach(t => {
    document.getElementById(`tab-content-${t}`).classList.toggle('hidden', t !== name);
    document.getElementById(`tab-${t}`).classList.toggle('active', t === name);
  });
  const view = document.querySelector('#app .view');
  if (view) view.classList.toggle('qa-active', name === 'qa');
  if (name === 'qa') {
    renderQAHistory(currentSlug);
    renderQAHeader(_currentRecord);
    const input = document.getElementById('qa-input');
    if (input) {
      input.removeEventListener('keydown', handleQAKeydown);
      input.addEventListener('keydown', handleQAKeydown);
    }
  }
}

function handleQAKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    sendQA();
  }
}

function renderQAHeader(record) {
  const nameEl  = document.getElementById('qa-company-name');
  const jobEl   = document.getElementById('qa-company-job');
  const panel   = document.getElementById('qa-skill-panel');
  const trigger = document.querySelector('.qa-skill-trigger');
  if (!nameEl || !record) return;

  // 트리거 또는 패널 위에 마우스가 있는 동안 패널 유지 (CSS hover 대신 JS 사용)
  let _hideTimer = null;
  const showPanel = () => { clearTimeout(_hideTimer); panel?.classList.add('visible'); };
  const hidePanel = () => { _hideTimer = setTimeout(() => panel?.classList.remove('visible'), 120); };
  [trigger, panel].forEach(el => {
    if (!el) return;
    el.addEventListener('mouseenter', showPanel);
    el.addEventListener('mouseleave', hidePanel);
  });

  const fm = record.frontmatter;
  nameEl.textContent = fm.company_name || '';
  if (jobEl) jobEl.textContent = fm.job_title || '';

  if (!panel) return;
  const req      = fm.required_skills || [];
  const strength = fm.strengths || [];
  const gaps     = fm.gaps || [];

  const makeList = (items, cls) => items.length
    ? `<ul class="${cls}">${items.map(i => `<li>${escHtml(i)}</li>`).join('')}</ul>`
    : '<p style="font-size:12px;color:#9ca3af">없음</p>';

  const score = fm.fit_score;
  const label = fm.fit_label || '';
  const badgeClass = score === null || score === undefined ? 'score-none'
    : score >= 70 ? 'score-high' : score >= 50 ? 'score-mid' : 'score-low';
  const scoreHtml = score !== null && score !== undefined
    ? `<span class="score-badge ${badgeClass}" style="font-size:13px;padding:3px 10px;">${score}점 ${escHtml(label)}</span>`
    : `<span class="score-badge score-none" style="font-size:13px;padding:3px 10px;">미평가</span>`;

  panel.innerHTML = `
    <div class="qa-skill-panel-section" style="border-bottom:1px solid #f0f0f0;padding-bottom:12px;margin-bottom:12px;">
      <div class="qa-skill-panel-title">🎯 적합도 점수</div>
      ${scoreHtml}
    </div>
    <div class="qa-skill-panel-section">
      <div class="qa-skill-panel-title">📋 공고 요구 스킬</div>
      ${makeList(req, 'skill-req')}
    </div>
    <div class="qa-skill-panel-section">
      <div class="qa-skill-panel-title">💪 내 강점</div>
      ${makeList(strength, 'skill-strength')}
    </div>
    <div class="qa-skill-panel-section">
      <div class="qa-skill-panel-title">⚠️ 약점</div>
      ${makeList(gaps, 'skill-gap')}
    </div>`;
}

function renderQAHistory(slug) {
  const container = document.getElementById('qa-messages');
  if (!container) return;
  container.innerHTML = '';
  (qaHistory[slug] || []).forEach(({ role, text }) => appendBubble('qa-messages', text, role));
}

function fillEditForm(fm, body) {
  const form = document.getElementById('edit-form');
  if (!form) return;
  const fields = ['company_name', 'job_title', 'source_url', 'location', 'employee_count',
    'stability', 'investment_stage', 'jobplanet_score', 'status'];
  fields.forEach(f => {
    const el = form.elements[f];
    if (el) el.value = fm[f] !== null && fm[f] !== undefined ? fm[f] : '';
  });
  if (form.elements['tech_stack']) form.elements['tech_stack'].value = (fm.tech_stack || []).join(', ');
  if (form.elements['tags']) form.elements['tags'].value = (fm.tags || []).join(', ');
  if (form.elements['body']) {
    const bodyEl = form.elements['body'];
    bodyEl.value = body || '';
    const preview = document.getElementById('md-preview');
    if (preview) {
      const update = () => {
        preview.innerHTML = parseMarkdown(bodyEl.value || '');
        applyHljs(preview);
      };
      update();
      bodyEl.removeEventListener('input', bodyEl._mdUpdate);
      bodyEl._mdUpdate = update;
      bodyEl.addEventListener('input', update);
    }
  }
}

async function saveCompany(event) {
  event.preventDefault();
  const form = event.target;
  const get = name => form.elements[name]?.value || '';
  const getList = name => get(name).split(',').map(s => s.trim()).filter(Boolean);

  const fm = {
    company_name: get('company_name'),
    job_title: get('job_title'),
    source_url: get('source_url') || null,
    location: get('location') || null,
    employee_count: get('employee_count') || null,
    stability: get('stability') || null,
    investment_stage: get('investment_stage') || null,
    jobplanet_score: get('jobplanet_score') ? parseFloat(get('jobplanet_score')) : null,
    status: get('status') || '미지원',
    tech_stack: getList('tech_stack'),
    tags: getList('tags'),
  };
  const body = get('body');

  try {
    await api(`/companies/${encodeURIComponent(currentSlug)}`, {
      method: 'PUT',
      body: JSON.stringify({ frontmatter: fm, body }),
    });
    alert('저장되었습니다.');
    switchTab('info');
    initDetail(currentSlug);
  } catch (e) {
    alert('저장 실패: ' + e.message);
  }
}

async function deleteCompany() {
  if (!confirm('이 회사를 삭제하시겠습니까?')) return;
  try {
    await api(`/companies/${encodeURIComponent(currentSlug)}`, { method: 'DELETE' });
    navigate('dashboard');
  } catch (e) {
    showToast('삭제 실패: ' + e.message, 'error');
  }
}

async function refitCompany() {
  const btn = document.querySelector('button[onclick="refitCompany()"]');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 재평가 중...'; }
  try {
    await api(`/companies/${encodeURIComponent(currentSlug)}/refit`, { method: 'POST', body: '{}' });
    showToast('적합도 재평가 완료!');
    initDetail(currentSlug);
  } catch (e) {
    showToast('재평가 실패: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🎯 적합도 재평가'; }
  }
}

async function syncWanted() {
  const form = document.getElementById('edit-form');
  const sourceUrl = form?.elements['source_url']?.value?.trim() || '';
  if (sourceUrl && !sourceUrl.includes('wanted.co.kr')) {
    alert('원티드 URL만 지원합니다. (wanted.co.kr)');
    return;
  }
  try {
    const body = sourceUrl ? JSON.stringify({ source_url: sourceUrl }) : '{}';
    const result = await api(`/companies/${encodeURIComponent(currentSlug)}/sync-wanted`, { method: 'POST', body });
    const updated = Object.keys(result.updated || {}).join(', ');
    alert(`원티드 동기화 완료!\n업데이트된 항목: ${updated || '없음'}`);
    initDetail(currentSlug);
    switchTab('edit');
  } catch (e) {
    alert('동기화 실패: ' + e.message);
  }
}

/* ── Q&A ─────────────────────────────────────────────────────────── */
async function sendQA() {
  const input = document.getElementById('qa-input');
  const question = input.value.trim();
  if (!question) return;
  input.value = '';

  if (!qaHistory[currentSlug]) qaHistory[currentSlug] = [];
  qaHistory[currentSlug].push({ role: 'user', text: question });
  saveQAHistory();
  appendBubble('qa-messages', question, 'user');
  const assistantBubble = appendBubble('qa-messages', '', 'assistant');

  const token = localStorage.getItem(TOKEN_KEY);
  const makeFetch = () => fetch(`/api/companies/${encodeURIComponent(currentSlug)}/qa`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
    body: JSON.stringify({ question }),
  });

  try {
    const fullText = await streamQA(makeFetch, assistantBubble);
    if (fullText) {
      qaHistory[currentSlug].push({ role: 'assistant', text: fullText });
      saveQAHistory();
    }
  } catch (e) {
    assistantBubble.textContent = `오류: ${e.message}`;
  }
}

async function sendCompareQA() {
  const input = document.getElementById('compare-qa-input');
  const question = input.value.trim();
  if (!question) return;
  input.value = '';

  appendBubble('compare-qa-messages', question, 'user');
  const assistantBubble = appendBubble('compare-qa-messages', '', 'assistant');

  const token = localStorage.getItem(TOKEN_KEY);
  const makeFetch = () => fetch('/api/companies/qa', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
    body: JSON.stringify({ slugs: compareTargets, question }),
  });

  try {
    await streamQA(makeFetch, assistantBubble);
  } catch (e) {
    assistantBubble.textContent = `오류: ${e.message}`;
  }
}

function appendBubble(containerId, text, role) {
  const container = document.getElementById(containerId);
  const bubble = document.createElement('div');
  bubble.className = `qa-bubble ${role}`;
  if (role === 'assistant' && text) bubble.innerHTML = parseMarkdown(text);
  else bubble.textContent = text;
  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
  return bubble;
}

const SSE_MAX_RETRIES = 2;

async function streamQA(fetchFn, bubble) {
  let lastError;
  for (let attempt = 0; attempt <= SSE_MAX_RETRIES; attempt++) {
    if (attempt > 0) {
      bubble.textContent = `연결이 끊겼습니다. 재연결 중... (${attempt}/${SSE_MAX_RETRIES})`;
      await new Promise(r => setTimeout(r, 1000 * attempt));
    }
    try {
      const res = await fetchFn();
      if (!res.ok) {
        if (res.status === 503 || res.status === 429) {
          throw new Error(`HTTP ${res.status}`);
        }
        const err = await res.json().catch(() => ({ detail: '서버 오류' }));
        bubble.textContent = `오류: ${err.detail || '응답 실패'}`;
        return null;
      }
      bubble.textContent = '';
      return await consumeSSE(res, bubble);
    } catch (e) {
      lastError = e;
    }
  }
  bubble.textContent = `오류: 연결 실패 (${lastError?.message})`;
  return null;
}

async function consumeSSE(res, bubble) {
  const reader = res.body.getReader();
  _activeSSEReader = reader;
  const decoder = new TextDecoder();
  let buffer = '';
  let fullText = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        const payload = line.slice(5).trim();
        if (payload === '[DONE]') return fullText;
        try {
          const { text, error } = JSON.parse(payload);
          if (error) { bubble.textContent = `오류: ${error}`; break; }
          fullText += text;
          bubble.innerHTML = parseMarkdown(fullText);
          bubble.scrollIntoView({ block: 'end' });
        } catch (_) {}
      }
    }
  } catch (e) {
    // navigate()에 의해 cancel()된 경우 AbortError — 정상 종료로 처리
    if (e.name !== 'AbortError') throw e;
  } finally {
    if (_activeSSEReader === reader) _activeSSEReader = null;
  }
  return fullText;
}

/* ── 회사 추가 ────────────────────────────────────────────────────── */
function initAdd() {}

function switchAddTab(name) {
  ['url', 'text', 'image'].forEach(t => {
    document.getElementById(`add-${t}`).classList.toggle('hidden', t !== name);
    document.getElementById(`add-tab-${t}`).classList.toggle('active', t === name);
  });
}

function onImageSelect() {
  const input = document.getElementById('image-input');
  const preview = document.getElementById('image-preview');
  if (!preview) return;
  preview.innerHTML = '';
  for (const file of input.files) {
    const url = URL.createObjectURL(file);
    const wrap = document.createElement('div');
    wrap.className = 'image-thumb-wrap';
    const img = document.createElement('img');
    img.src = url;
    img.className = 'image-thumb';
    const label = document.createElement('span');
    label.className = 'image-thumb-label';
    label.textContent = file.name;
    wrap.appendChild(img);
    wrap.appendChild(label);
    preview.appendChild(wrap);
  }
}

async function submitImage() {
  const input = document.getElementById('image-input');
  if (!input?.files?.length) return alert('이미지를 선택해주세요.');
  setProgress('🖼️ 이미지 분석 중...');
  const token = localStorage.getItem(TOKEN_KEY);
  const formData = new FormData();
  for (const file of input.files) formData.append('files', file);
  try {
    const res = await fetch('/api/companies/from-image', {
      method: 'POST',
      headers: token ? { 'Authorization': `Bearer ${token}` } : {},
      body: formData,
    });
    setProgress(null);
    if (res.status === 401) { localStorage.removeItem(TOKEN_KEY); navigate('login', null, true); return; }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      showToast(typeof err.detail === 'string' ? err.detail : '요청 실패', 'error');
      return;
    }
    const record = await res.json();
    showToast(`${record.frontmatter.company_name} 분석 완료!`);
    navigate('detail', record.slug);
  } catch (e) {
    setProgress(null);
    showToast(e.message, 'error');
  }
}

const _PROGRESS_STEPS = [
  { at: 5,  msg: '🧠 구조화 데이터 추출 중...' },
  { at: 10, msg: '🏢 잡플래닛 평점 수집 중...' },
  { at: 16, msg: '✍️ 마크다운 본문 생성 중...' },
  { at: 24, msg: '🎯 이력서와 적합도 비교 중...' },
  { at: 38, msg: '⏳ 거의 다 됐어요...' },
];
let _progressTimer = null;

function setProgress(msg) {
  const box = document.getElementById('add-progress');
  const msgEl = document.getElementById('progress-msg');
  const elapsedEl = document.getElementById('progress-elapsed');
  const barEl = document.getElementById('progress-bar');
  if (!box) return;

  if (_progressTimer) { clearInterval(_progressTimer); _progressTimer = null; }

  if (msg) {
    box.classList.remove('hidden');
    msgEl.textContent = msg;
    if (elapsedEl) elapsedEl.textContent = '0초 경과';
    if (barEl) barEl.style.width = '0%';

    let elapsed = 0;
    _progressTimer = setInterval(() => {
      elapsed++;
      if (elapsedEl) elapsedEl.textContent = `${elapsed}초 경과`;
      const step = [..._PROGRESS_STEPS].reverse().find(s => elapsed >= s.at);
      if (step && msgEl) msgEl.textContent = step.msg;
      if (barEl) {
        const pct = elapsed < 38
          ? (elapsed / 38) * 80
          : Math.min(90, 80 + (elapsed - 38) * 0.12);
        barEl.style.width = pct + '%';
      }
    }, 1000);
  } else {
    if (barEl) barEl.style.width = '100%';
    setTimeout(() => {
      box.classList.add('hidden');
      if (barEl) barEl.style.width = '0%';
    }, 500);
  }
}

async function submitUrl() {
  const url = document.getElementById('url-input').value.trim();
  if (!url) return alert('URL을 입력해주세요.');
  setProgress('🔍 페이지 스크래핑 중...');
  const token = localStorage.getItem(TOKEN_KEY);
  try {
    const res = await fetch('/api/companies/from-url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
      body: JSON.stringify({ url }),
    });
    setProgress(null);
    if (res.status === 409) {
      const { detail } = await res.json();
      showToast(`이미 등록된 공고입니다 (${detail.name}). 해당 페이지로 이동합니다.`, 'error');
      navigate('detail', detail.slug);
      return;
    }
    if (res.status === 401) { localStorage.removeItem(TOKEN_KEY); navigate('login'); return; }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      showToast(typeof err.detail === 'string' ? err.detail : '요청 실패', 'error');
      return;
    }
    const record = await res.json();
    showToast(`${record.frontmatter.company_name} 분석 완료!`);
    navigate('detail', record.slug);
  } catch (e) {
    setProgress(null);
    showToast(e.message, 'error');
  }
}

async function submitText() {
  const company_name = document.getElementById('text-company').value.trim();
  const job_title = document.getElementById('text-job').value.trim();
  if (!company_name) return alert('회사명을 입력해주세요.');
  if (!job_title) return alert('직무명을 입력해주세요.');
  const text = document.getElementById('text-input').value.trim();
  const source_url = document.getElementById('text-url').value.trim() || null;
  const payload = { company_name, job_title, source_url, text };
  setProgress(text ? '🤖 AI 분석 중...' : '💾 저장 중...');
  try {
    const record = await api('/companies/from-text', { method: 'POST', body: JSON.stringify(payload) });
    setProgress(null);
    showToast(`${record.frontmatter.company_name} ${text ? '분석 완료!' : '저장 완료!'}`);
    navigate('detail', record.slug);
  } catch (e) {
    setProgress(null);
    showToast(e.message, 'error');
  }
}


/* ── 비교 뷰 ─────────────────────────────────────────────────────── */
async function initCompare(slugs) {
  let records;
  try {
    records = await api(`/companies/compare?slugs=${slugs.join(',')}`);
  } catch (e) {
    showToast('비교 데이터 로딩 실패: ' + e.message, 'error');
    navigate('dashboard');
    return;
  }

  const FIELDS = [
    ['적합도', r => r.frontmatter.fit_score !== null ? r.frontmatter.fit_score + '점' : '-', 'fit_score'],
    ['결론', r => escHtml(r.frontmatter.fit_label || '-')],
    ['경력 요구', r => escHtml(r.frontmatter.experience_required || '-')],
    ['안정성', r => escHtml(r.frontmatter.stability || '-')],
    ['임직원', r => escHtml(r.frontmatter.employee_count || '-')],
    ['위치', r => escHtml(r.frontmatter.location || '-')],
    ['기술스택', r => {
      const items = r.frontmatter.tech_stack || [];
      return items.length ? items.map(s => `<span class="compare-chip">${escHtml(s)}</span>`).join('') : '-';
    }],
    ['투자단계', r => escHtml(r.frontmatter.investment_stage || '-')],
    ['잡플래닛', r => {
      const score = r.frontmatter.jobplanet_score;
      const cnt = r.frontmatter.jobplanet_review_count;
      if (score == null) return '-';
      return escHtml(cnt != null ? `${score} (${cnt}건)` : String(score));
    }, 'jobplanet_score'],
    ['지원상태', r => escHtml(r.frontmatter.status || '-')],
    ['강점', r => {
      const items = r.frontmatter.strengths || [];
      return items.length ? '<div class="compare-list-wrap"><ul class="compare-list">' + items.map(s => `<li>${escHtml(s)}</li>`).join('') + '</ul></div>' : '-';
    }],
    ['갭', r => {
      const items = r.frontmatter.gaps || [];
      return items.length ? '<div class="compare-list-wrap"><ul class="compare-list compare-list--gap">' + items.map(s => `<li>${escHtml(s)}</li>`).join('') + '</ul></div>' : '-';
    }],
  ];

  // 하이라이트 대상 필드의 최고값 계산
  const highlights = {};
  for (const [, , field] of FIELDS) {
    if (!field) continue;
    const vals = records.map(r => parseFloat(r.frontmatter[field])).filter(v => !isNaN(v));
    if (vals.length > 1) highlights[field] = Math.max(...vals);
  }

  const table = document.getElementById('compare-table');
  const headerRow = `<tr><th class="row-label">항목</th>${records.map(r =>
    `<th>${escHtml(r.frontmatter.display_name || r.frontmatter.company_name)}<br/><small>${escHtml(r.frontmatter.job_title || '')}</small></th>`
  ).join('')}</tr>`;

  const rows = FIELDS.map(([label, fn, field]) =>
    `<tr><td class="row-label">${label}</td>${records.map(r => {
      const val = fn(r);
      const isTop = field && highlights[field] != null &&
        parseFloat(r.frontmatter[field]) === highlights[field];
      return `<td${isTop ? ' class="highlight-best"' : ''}>${val}</td>`;
    }).join('')}</tr>`
  ).join('');

  table.innerHTML = `<thead>${headerRow}</thead><tbody>${rows}</tbody>`;
}

/* ── 설정 ─────────────────────────────────────────────────────────── */
async function initSettings() {
  let profileStatus, currentSettings;
  try {
    [profileStatus, currentSettings] = await Promise.all([
      api('/profile/status'),
      api('/settings'),
    ]);
  } catch (e) {
    showToast('설정 로딩 실패: ' + e.message, 'error');
    return;
  }

  const statusMsg = document.getElementById('profile-status-msg');
  const exportProfileDdBtn = document.getElementById('export-profile-dd-btn');
  setUploadBoxCollapsed(profileStatus.exists);
  if (profileStatus.exists) {
    statusMsg.textContent = '✅ 프로필이 등록되어 있습니다.';
    statusMsg.style.color = '#065f46';
    if (exportProfileDdBtn) exportProfileDdBtn.disabled = false;
    try {
      const profileRecord = await api('/profile');
      window._profileRecord = profileRecord;
      const previewEl = document.getElementById('profile-preview');
      if (!previewEl) throw new Error();
      if (profileRecord.body && profileRecord.body.trim()) {
        previewEl.innerHTML = parseMarkdown(profileRecord.body);
        applyHljs(previewEl);
      } else {
        const fm = profileRecord.frontmatter || {};
        const lines = [];
        if (fm.name) lines.push(`# ${fm.name}${fm.experience_years ? `  ·  경력 ${fm.experience_years}년` : ''}`);
        if (fm.summary) lines.push(`\n${fm.summary}`);
        if (fm.education) lines.push(`\n## 학력\n${fm.education}`);
        if ((fm.experience_roles||[]).length) lines.push(`\n## 직무 역할\n${fm.experience_roles.map(r=>`- ${r}`).join('\n')}`);
        if ((fm.tech_skills||[]).length) lines.push(`\n## 기술 스킬\n${fm.tech_skills.map(s=>`- ${s}`).join('\n')}`);
        if ((fm.domains||[]).length) lines.push(`\n## 경험 도메인\n${fm.domains.map(s=>`- ${s}`).join('\n')}`);
        if ((fm.soft_skills||[]).length) lines.push(`\n## 소프트 스킬\n${fm.soft_skills.map(s=>`- ${s}`).join('\n')}`);
        if (fm.preferred_location?.length) lines.push(`\n## 선호 근무지\n${fm.preferred_location.join(', ')}`);
        if (fm.preferred_employment_type) lines.push(`\n## 선호 고용형태\n${fm.preferred_employment_type}`);
        if (fm.preferred_min_salary) lines.push(`\n## 희망 최소 연봉\n${fm.preferred_min_salary}만원`);
        previewEl.innerHTML = parseMarkdown(lines.join('\n'));
      }
      const editBtn = document.getElementById('profile-edit-btn');
      if (editBtn) editBtn.style.display = '';
    } catch (e) { console.error('프로필 미리보기 로딩 실패:', e); }
  } else {
    statusMsg.textContent = '⚠️ 프로필이 없습니다. PDF를 업로드해주세요.';
  }

  const providerInfo = document.getElementById('current-provider-info');
  if (providerInfo) {
    providerInfo.textContent = `현재 Provider: ${currentSettings.provider}`;
  }

  const providerSelect = document.getElementById('provider-select');
  if (providerSelect) providerSelect.value = currentSettings.provider;
  syncProviderModels();

  // 캐시된 모델 목록 복원
  const p = currentSettings.provider;
  applyModelCache(p, currentSettings[`${p}_high_model`], currentSettings[`${p}_light_model`]);

  // 추가 설명 복원
  const extraNoteEl = document.getElementById('profile-extra-note');
  if (extraNoteEl) extraNoteEl.value = localStorage.getItem('profile-extra-note') || '';

  ['claude-high-model', 'claude-light-model', 'openai-high-model', 'openai-light-model'].forEach(id => {
    const el = document.getElementById(id);
    const key = id.replace(/-/g, '_');
    if (el) el.value = currentSettings[key] || '';
  });

  try {
    const criteriaData = await api('/eval-criteria');
    const criteriaEl = document.getElementById('eval-criteria-input');
    if (criteriaEl) criteriaEl.value = criteriaData.text || '';
  } catch (e) { console.error('평가 기준 로딩 실패:', e); }

  try {
    const usageData = await api('/usage');
    renderUsage(usageData);
  } catch (e) { console.error('사용량 로딩 실패:', e); }
}

function renderUsage(data) {
  const tbody = document.getElementById('usage-tbody');
  const empty = document.getElementById('usage-empty');
  const summary = document.getElementById('usage-summary');
  if (!tbody) return;

  const { entries = [], total_log_count = 0, total_cost_usd = 0, total_input_tokens = 0, total_output_tokens = 0 } = data;

  if (summary) {
    const countNote = total_log_count > entries.length
      ? ` &nbsp;<span style="color:#6b7280;font-weight:400">(최근 ${entries.length}건 표시 / 전체 ${total_log_count}건 누적)</span>`
      : '';
    summary.innerHTML = `누적 비용: <strong>$${total_cost_usd.toFixed(4)}</strong> &nbsp;·&nbsp; 입력 ${total_input_tokens.toLocaleString()} 토큰 &nbsp;·&nbsp; 출력 ${total_output_tokens.toLocaleString()} 토큰${countNote}`;
  }

  if (!entries.length) {
    tbody.innerHTML = '';
    empty?.classList.remove('hidden');
    return;
  }
  empty?.classList.add('hidden');

  tbody.innerHTML = entries.map(e => {
    const ts = (e.ts || '').replace('T', ' ');
    const modelShort = (e.model || '').replace('claude-', '').replace('gpt-', '');
    const cost = typeof e.cost_usd === 'number' ? '$' + e.cost_usd.toFixed(5) : '-';
    return `<tr>
      <td style="white-space:nowrap;color:#6b7280;font-size:12px">${escHtml(ts)}</td>
      <td>${escHtml(e.operation || '-')}</td>
      <td style="font-size:12px;color:#6b7280">${escHtml(modelShort)}</td>
      <td style="text-align:right;font-size:12px">${(e.input_tokens || 0).toLocaleString()}</td>
      <td style="text-align:right;font-size:12px">${(e.output_tokens || 0).toLocaleString()}</td>
      <td style="text-align:right;font-weight:600">${escHtml(cost)}</td>
    </tr>`;
  }).join('');
}

async function saveEvalCriteria() {
  const text = document.getElementById('eval-criteria-input')?.value || '';
  try {
    await api('/eval-criteria', { method: 'PUT', body: JSON.stringify({ text }) });
    showToast('평가 기준이 저장되었습니다.');
  } catch (e) {
    showToast('저장 실패: ' + e.message, 'error');
  }
}

function toggleProfileEditor() {
  const wrap = document.getElementById('profile-editor-wrap');
  const preview = document.getElementById('profile-preview');
  const isHidden = wrap.classList.contains('hidden');
  if (isHidden) {
    const body = window._profileRecord?.body || '';
    const input = document.getElementById('profile-editor-input');
    const editorPreview = document.getElementById('profile-editor-preview');
    input.value = body;
    const update = () => {
      editorPreview.innerHTML = parseMarkdown(input.value || '');
      applyHljs(editorPreview);
    };
    update();
    input.removeEventListener('input', input._mdUpdate);
    input._mdUpdate = update;
    input.addEventListener('input', update);
    wrap.classList.remove('hidden');
    preview.classList.add('hidden');
    document.getElementById('profile-edit-btn').textContent = '✕ 닫기';
  } else {
    wrap.classList.add('hidden');
    preview.classList.remove('hidden');
    document.getElementById('profile-edit-btn').textContent = '✏️ 직접 편집';
  }
}

async function saveProfileEdit() {
  if (!window._profileRecord) return;
  const body = document.getElementById('profile-editor-input').value;
  try {
    const updated = await api('/profile', {
      method: 'PUT',
      body: JSON.stringify({ frontmatter: window._profileRecord.frontmatter, body }),
    });
    window._profileRecord = updated;
    const previewEl = document.getElementById('profile-preview');
    previewEl.innerHTML = parseMarkdown(updated.body || '');
    applyHljs(previewEl);
    toggleProfileEditor();
    showToast('프로필이 저장되었습니다.');
  } catch (e) {
    showToast('저장 실패: ' + e.message, 'error');
  }
}

function handleProfileDrop(e) {
  e.preventDefault();
  document.getElementById('pdf-dropzone').classList.remove('drag-over');
  const files = [...e.dataTransfer.files].filter(f => f.type === 'application/pdf');
  if (!files.length) { showToast('PDF 파일만 업로드 가능합니다.', 'error'); return; }
  const dt = new DataTransfer();
  files.forEach(f => dt.items.add(f));
  document.getElementById('pdf-input').files = dt.files;
  updateDropzoneLabel();
}

function updateDropzoneLabel() {
  const input = document.getElementById('pdf-input');
  const label = document.getElementById('pdf-dropzone-label');
  if (!label) return;
  if (input.files.length === 0) {
    label.textContent = '📂 클릭하거나 PDF를 여기에 드래그하세요 (복수 가능)';
  } else {
    const names = [...input.files].map(f => f.name).join(', ');
    label.textContent = `✅ ${input.files.length}개 선택됨: ${names}`;
  }
}

async function uploadProfile() {
  const input = document.getElementById('pdf-input');
  if (!input.files.length) return alert('PDF 파일을 선택해주세요.');

  const progressEl = document.getElementById('upload-progress');
  const msgEl = document.getElementById('upload-msg');
  progressEl.classList.remove('hidden');
  msgEl.textContent = '📤 업로드 중...';

  const formData = new FormData();
  for (const file of input.files) formData.append('files', file);
  const extraNote = document.getElementById('profile-extra-note')?.value.trim() || '';
  if (extraNote) formData.append('extra_note', extraNote);
  const maxTokens = parseInt(document.getElementById('profile-max-tokens')?.value || '8192', 10);
  formData.append('max_tokens', String(maxTokens));

  const token = localStorage.getItem(TOKEN_KEY);
  try {
    const res = await fetch('/api/profile/upload', {
      method: 'POST',
      headers: token ? { 'Authorization': `Bearer ${token}` } : {},
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: '업로드 실패' }));
      throw new Error(err.detail);
    }
    msgEl.textContent = '✅ 프로필 분석 완료!';
    setTimeout(() => { progressEl.classList.add('hidden'); initSettings(); }, 1500);
  } catch (e) {
    progressEl.classList.add('hidden');
    alert('업로드 실패: ' + e.message);
  }
}

async function exportProfile() {
  const token = localStorage.getItem(TOKEN_KEY);
  try {
    const res = await fetch('/api/profile/export', {
      headers: token ? { 'Authorization': `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new Error('다운로드 실패');
    const blob = await res.blob();
    const disposition = res.headers.get('Content-Disposition') || '';
    const nameMatch = disposition.match(/filename\*?=(?:UTF-8'')?(.+)/i);
    const filename = nameMatch ? decodeURIComponent(nameMatch[1]) : 'profile.md';
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    showToast('내보내기 실패: ' + e.message, 'error');
  }
}

function syncProviderModels() {
  const provider = document.getElementById('provider-select')?.value;
  document.getElementById('model-inputs-claude').style.display = provider === 'claude' ? '' : 'none';
  document.getElementById('model-inputs-openai').style.display = provider === 'openai' ? '' : 'none';
}

// 알려진 모델 입력 단가 ($/1M tokens) — 미등록 모델은 이름 패턴으로 추정
const MODEL_INPUT_PRICE = {
  'claude-opus': 5, 'claude-sonnet': 3, 'claude-haiku': 1,
  'o3': 20, 'o1': 15, 'gpt-4o': 2.5, 'gpt-4o-mini': 0.15,
};

function getModelPrice(id) {
  for (const [key, price] of Object.entries(MODEL_INPUT_PRICE)) {
    if (id.includes(key)) return price;
  }
  return null;
}

function getTierStyle(price) {
  if (price === null) return { bar: '#d1d5db', label: '미확인' };
  if (price >= 4)   return { bar: '#ef4444', label: '고가' };
  if (price >= 1.5) return { bar: '#f59e0b', label: '중간' };
  return              { bar: '#22c55e', label: '저가' };
}

function renderModelTierView(models, provider) {
  const tierView = document.getElementById('model-tier-view');
  if (!tierView) return;
  const sorted = [...models].sort((a, b) => (getModelPrice(b) ?? 0) - (getModelPrice(a) ?? 0));
  tierView.innerHTML = '<div style="font-size:12px;color:#6b7280;margin-bottom:8px;font-weight:600">비용 순서 (비쌈 → 저렴)</div>'
    + sorted.map(m => {
      const price = getModelPrice(m);
      const { bar, label } = getTierStyle(price);
      const tooltip = price !== null ? `입력 $${price}/1M tokens` : '단가 정보 없음';
      return `<div title="${tooltip}" style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid #f3f4f6;cursor:default">`
        + `<span style="width:4px;height:20px;border-radius:2px;background:${bar};flex-shrink:0"></span>`
        + `<span style="font-size:13px;color:#111827;flex:1">${escHtml(m)}</span>`
        + `<span style="font-size:11px;color:#9ca3af;white-space:nowrap">${label}</span>`
        + `</div>`;
    }).join('');
}

function applyModelListToDropdowns(models, provider, currentHigh, currentLight) {
  const highId = `${provider}-high-model`;
  const lightId = `${provider}-light-model`;
  [[highId, currentHigh], [lightId, currentLight]].forEach(([id, current]) => {
    const wrap = document.getElementById(id)?.parentElement;
    if (!wrap) return;
    const labelText = wrap.childNodes[0].textContent;
    const sel = document.createElement('select');
    sel.id = id;
    models.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m; opt.textContent = m;
      if (m === current) opt.selected = true;
      sel.appendChild(opt);
    });
    wrap.innerHTML = '';
    wrap.appendChild(document.createTextNode(labelText));
    wrap.appendChild(sel);
  });
}

function loadModelCache(provider) {
  try {
    const raw = localStorage.getItem(`model-cache-${provider}`);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

function saveModelCache(provider, models) {
  localStorage.setItem(`model-cache-${provider}`, JSON.stringify({
    models,
    updatedAt: new Date().toISOString(),
  }));
}

function applyModelCache(provider, currentHigh, currentLight) {
  const cache = loadModelCache(provider);
  if (!cache) return false;
  applyModelListToDropdowns(cache.models, provider, currentHigh, currentLight);
  renderModelTierView(cache.models, provider);
  const updatedEl = document.getElementById('model-list-updated');
  if (updatedEl) {
    const d = new Date(cache.updatedAt);
    updatedEl.textContent = `마지막 업데이트: ${d.toLocaleDateString('ko-KR')} ${d.toLocaleTimeString('ko-KR')}`;
  }
  return true;
}

async function fetchModelList() {
  const provider = document.getElementById('provider-select')?.value;
  if (!provider) return;
  const btn = document.querySelector('[onclick="fetchModelList()"]');
  if (btn) { btn.disabled = true; btn.textContent = '불러오는 중...'; }
  try {
    const data = await api(`/models?provider=${provider}`);
    const models = data.models;
    const currentHigh = document.getElementById(`${provider}-high-model`)?.value || '';
    const currentLight = document.getElementById(`${provider}-light-model`)?.value || '';

    saveModelCache(provider, models);
    applyModelListToDropdowns(models, provider, currentHigh, currentLight);
    renderModelTierView(models, provider);

    const updatedEl = document.getElementById('model-list-updated');
    if (updatedEl) updatedEl.textContent = `마지막 업데이트: ${new Date().toLocaleDateString('ko-KR')} ${new Date().toLocaleTimeString('ko-KR')}`;

    showToast(`${models.length}개 모델 로드 완료`, 'success');
  } catch (e) {
    showToast('모델 목록 조회 실패: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '모델 목록 불러오기'; }
  }
}

async function saveSettings() {
  const payload = {
    provider: document.getElementById('provider-select')?.value,
    claude_high_model: document.getElementById('claude-high-model')?.value || null,
    claude_light_model: document.getElementById('claude-light-model')?.value || null,
    openai_high_model: document.getElementById('openai-high-model')?.value || null,
    openai_light_model: document.getElementById('openai-light-model')?.value || null,
  };
  try {
    await api('/settings', { method: 'PUT', body: JSON.stringify(payload) });
    alert('설정이 저장되었습니다.');
    initSettings();
  } catch (e) {
    alert('저장 실패: ' + e.message);
  }
}

/* ── 토스트 알림 ──────────────────────────────────────────────────── */
function setUploadBoxCollapsed(collapsed) {
  const body = document.getElementById('upload-box-body');
  const chevron = document.getElementById('upload-box-chevron');
  if (!body) return;
  if (collapsed) {
    body.classList.add('hidden');
    if (chevron) chevron.textContent = '▸';
  } else {
    body.classList.remove('hidden');
    if (chevron) chevron.textContent = '▾';
  }
}

function toggleUploadBox() {
  const body = document.getElementById('upload-box-body');
  if (!body) return;
  setUploadBoxCollapsed(!body.classList.contains('hidden'));
}

function toggleExportDropdown(e) {
  e.stopPropagation();
  const panel = document.getElementById('export-dropdown-panel');
  if (!panel) return;
  const isHidden = panel.classList.toggle('hidden');
  if (!isHidden) {
    const close = (ev) => {
      if (!document.getElementById('export-dropdown-wrap')?.contains(ev.target)) {
        panel.classList.add('hidden');
        document.removeEventListener('click', close);
      }
    };
    document.addEventListener('click', close, { once: true });
  }
}

async function exportZip() {
  document.getElementById('export-dropdown-panel')?.classList.add('hidden');
  const token = localStorage.getItem(TOKEN_KEY);
  const includePdf = document.getElementById('export-include-pdf')?.checked ? 'true' : 'false';
  const includeLog = document.getElementById('export-include-log')?.checked ? 'true' : 'false';
  try {
    const res = await fetch(`/api/export/zip?include_pdf=${includePdf}&include_log=${includeLog}`, {
      headers: token ? { 'Authorization': `Bearer ${token}` } : {},
    });
    if (!res.ok) { showToast('ZIP 내보내기 실패', 'error'); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `job-fitcheck_backup_${new Date().toISOString().slice(0, 10)}.zip`;
    a.click();
    URL.revokeObjectURL(url);
    showToast('백업 ZIP 다운로드 완료');
  } catch (e) {
    showToast('ZIP 내보내기 실패: ' + e.message, 'error');
  }
}

async function exportCSV() {
  const token = localStorage.getItem(TOKEN_KEY);
  try {
    const res = await fetch('/api/companies/export/csv', {
      headers: token ? { 'Authorization': `Bearer ${token}` } : {},
    });
    if (!res.ok) { showToast('CSV 내보내기 실패', 'error'); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `companies_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    showToast('CSV 내보내기 실패: ' + e.message, 'error');
  }
}

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

const EXCLUDED_LOG_LABELS = new Set(['분석 완료', '등록']);
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
      html += `<div class="tl-entry" onclick="navigate('detail', ${JSON.stringify(e.company.slug)})">
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
  const today = new Date().toISOString().slice(0, 10);

  let html = '';
  // Leading empty cells
  for (let i = 0; i < firstDay; i++) html += '<div class="cal-cell cal-cell--empty"></div>';

  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${_calYear}-${String(_calMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    const isToday = dateStr === today;
    const dayEntries = dateMap[dateStr] || [];
    const chips = dayEntries.slice(0, 3).map(e => {
      const color = _logColor(e.label);
      return `<div class="cal-chip" style="background:${color}" onclick="event.stopPropagation();navigate('detail',${JSON.stringify(e.company.slug)})" title="${escHtml(e.company.display_name)} — ${escHtml(e.company.job_title)} (${escHtml(e.label)})"><div class="cal-chip-name">${escHtml(e.company.display_name)}</div><div class="cal-chip-job">${escHtml(e.company.job_title)}</div></div>`;
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

function showToast(msg, type = 'success', duration = 5000) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    document.body.appendChild(container);
  }

  const icon = type === 'success' ? '✅' : '❌';
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span class="toast-icon">${icon}</span><span class="toast-msg"></span><button class="toast-close" onclick="this.closest('.toast').remove()">✕</button>`;
  toast.querySelector('.toast-msg').textContent = msg;
  container.appendChild(toast);

  // 완료음 (Web Audio API — 외부 파일 불필요)
  if (type === 'success') {
    try {
      _audioCtx = _audioCtx || new AudioContext();
      const ctx = _audioCtx;
      [880, 1100].forEach((freq, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        osc.frequency.value = freq;
        osc.type = 'sine';
        gain.gain.setValueAtTime(0.18, ctx.currentTime + i * 0.12);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + i * 0.12 + 0.18);
        osc.start(ctx.currentTime + i * 0.12);
        osc.stop(ctx.currentTime + i * 0.12 + 0.18);
      });
    } catch (_) {}
  }

  setTimeout(() => toast.remove(), duration);
}

/* ── 초기 로드 ────────────────────────────────────────────────────── */
window.addEventListener('DOMContentLoaded', () => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) {
    currentView = 'login';
    currentSlug = null;
    history.replaceState({ view: 'login', slug: null, compareTargets: [] }, '', window.location.pathname);
    render();
    return;
  }
  const { view, slug } = parseUrl();
  // /compare를 직접 접근했을 때 compareTargets가 없으면 대시보드로
  if (view === 'compare' && compareTargets.length === 0) {
    currentView = 'dashboard';
    history.replaceState({ view: 'dashboard', slug: null, compareTargets: [] }, '', '/');
  } else {
    currentView = view;
    currentSlug = slug;
    history.replaceState({ view, slug, compareTargets: compareTargets.slice() }, '', window.location.pathname);
  }
  render();
});
