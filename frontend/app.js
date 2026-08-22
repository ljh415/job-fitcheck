/* ── marked 확장: 단일 줄바꿈도 <br>로 렌더링 + ==텍스트== → <mark> 하이라이트 ── */
marked.setOptions({ breaks: true });

function parseMarkdown(text) {
  // "**따옴표**뒤텍스트"처럼 닫는 **의 앞이 구두점이고 뒤에 공백 없이 글자가 바로 이어지면
  // CommonMark 강조(emphasis) 판정 규칙상 marked가 볼드로 인식하지 못해 **가 그대로 노출되는
  // 경우가 있어(한국어 LLM 응답에서 자주 발생), marked 파싱 전에 **쌍을 직접 <strong>으로 치환한다.
  const normalized = (text || '').replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>');
  let html = marked.parse(normalized);
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
let compareQaHistory = [];
const TERMINATED = new Set(['탈락', '지원마감']);
let hideTerminated = localStorage.getItem('hide-terminated') !== 'false'; // 기본: 숨김
let filterPinnedOnly = false;
let _activeSSEReader = null;
let _audioCtx = null;
let _currentRecord = null;
let ragEnabled = false;
let ragConfiguredProviders = [];
let ragIncludeProfile = false;
// QnA 히스토리는 서버(qa_messages)가 진실 공급원 — 로컬 상태로 안 들고 있는다.
// localStorage의 옛 qaHistory는 1회성 마이그레이션 소스로만 씀(migrateQAHistoryIfNeeded 참고).

// 이 브라우저(기기)를 구분하는 안정적 ID — 최초 1회 생성해 영구 저장. 서버가 "이 슬러그에
// 메시지가 있는지"가 아니라 "이 기기가 이 슬러그를 이미 옮겼는지"로 멱등 판단하는 데 쓴다
// (v1.5.1 회귀 수정, 2026-08-22 — 슬러그 기준으로 스킵하면 다른 기기의 이력이 못 옮겨짐).
function getDeviceId() {
  let id = localStorage.getItem('job-fitcheck-device-id');
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem('job-fitcheck-device-id', id);
  }
  return id;
}

// job-fitcheck-qa-migrated-v2: v1.5.1(슬러그 단위 스킵) 시절 이미 완료 플래그가 찍혀 다시
// 호출되지 않던 기기를 위한 1회성 복구 트리거. 기존 완료 키는 그대로 두고 별도로 둔다 —
// 서버(migrate_qa_slug_history)가 내용 기반으로 중복을 걸러주므로 이미 성공한 기기가 다시
// 호출해도 안전하다(Codex 2차 리뷰로 발견, 2026-08-22).
async function migrateQAHistoryIfNeeded() {
  const migrated = localStorage.getItem('job-fitcheck-qa-migrated') === '1';
  const recovered = localStorage.getItem('job-fitcheck-qa-migrated-v2') === '1';
  if (migrated && recovered) return;
  const raw = localStorage.getItem('job-fitcheck-qa');
  const history = raw ? JSON.parse(raw) : {};
  if (Object.keys(history).length === 0) {
    localStorage.setItem('job-fitcheck-qa-migrated', '1');
    localStorage.setItem('job-fitcheck-qa-migrated-v2', '1');
    return;
  }
  try {
    await api('/companies/migrate-qa', {
      method: 'POST',
      body: JSON.stringify({ device_id: getDeviceId(), history }),
    });
    localStorage.setItem('job-fitcheck-qa-migrated', '1');
    localStorage.setItem('job-fitcheck-qa-migrated-v2', '1');
  } catch (e) {
    // 실패하면 플래그를 안 세워서 다음 로드 때 재시도 — 실패한 채로 표시만 남기면
    // 이 기기의 옛 대화가 영영 안 옮겨진다.
    console.warn('QnA 히스토리 마이그레이션 실패, 다음 로드 때 재시도:', e);
  }
}

function localDateString(d = new Date()) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
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
  if (view === 'rag') return '/rag';
  if (view === 'profile-version' && slug) return `/profile-version/${slug}`;
  if (view === 'fit-history' && slug) return `/fit-history/${slug}`;
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
  if (path === '/rag') return { view: 'rag', slug: null };
  const versionMatch = path.match(/^\/profile-version\/(\d+)$/);
  if (versionMatch) return { view: 'profile-version', slug: versionMatch[1] };
  const fitHistoryMatch = path.match(/^\/fit-history\/(\d+)$/);
  if (fitHistoryMatch) return { view: 'fit-history', slug: fitHistoryMatch[1] };
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
  // RAG가 비활성인데 히스토리에 남아있던 /rag 상태로 뒤로가기 하면 깨진 화면이 뜬다
  // (코드리뷰 5번, 2026-08-02).
  if (currentView === 'rag' && !ragEnabled) currentView = 'dashboard';
  render();
});

/* ── 이벤트 위임 (동적으로 렌더링되는 카드/행/칩의 클릭·변경 처리) ──────── */
// inline onclick/onchange에 slug 값을 직접 문자열로 삽입하면 저장형 XSS 위험이
// 있으므로, data-* 속성만 사용하고 실제 동작은 #app에 위임한 핸들러가 처리한다.
// #app은 render()가 innerHTML만 교체할 뿐 엘리먼트 자체는 교체되지 않으므로
// 뷰가 바뀌어도 리스너가 유지된다.
document.getElementById('app').addEventListener('click', (e) => {
  const pinBtn = e.target.closest('[data-action="toggle-pin"]');
  if (pinBtn) { togglePin(pinBtn.dataset.slug); return; }
  const noteArea = e.target.closest('[data-action="edit-version-note"]');
  if (noteArea) { editProfileVersionNote(Number(noteArea.dataset.id)); return; }
  if (e.target.closest('input, select, a')) return;
  const navEl = e.target.closest('[data-nav="detail"]');
  if (navEl) navigate('detail', navEl.dataset.slug);
});

document.getElementById('app').addEventListener('change', (e) => {
  if (e.target.matches('.row-check')) { onCheckChange(e.target); return; }
  if (e.target.matches('[data-action="status-change"]')) onStatusChange(e.target.dataset.slug, e.target);
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
  else if (currentView === 'rag') initRag();
  else if (currentView === 'profile-version') initProfileVersion(currentSlug);
  else if (currentView === 'fit-history') initFitHistoryDetail(currentSlug);
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
    stopInProgressPolling();
    navigate('login', null, true);
    throw new Error('인증이 필요합니다.');
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || '요청 실패');
  }
  return res.json();
}

/* ── 진행 중 분석 배너 ────────────────────────────────────────────── */
let _inProgressTimer = null;

async function pollInProgress() {
  const banner = document.getElementById('in-progress-banner');
  if (!banner) return;
  try {
    const { count } = await api('/analysis-in-progress');
    if (count > 0) {
      banner.textContent = `🧠 ${count}건 분석 중...`;
      banner.classList.remove('hidden');
    } else {
      banner.classList.add('hidden');
    }
  } catch (e) {
    // 인증 만료 등은 api()가 이미 로그인 화면 리다이렉트로 처리함 — 여기선 조용히 넘어감
  }
}

function startInProgressPolling() {
  if (_inProgressTimer) return;
  pollInProgress();
  _inProgressTimer = setInterval(pollInProgress, 7000);
}

/* ── RAG 활성화 여부(opt-in) ──────────────────────────────────────── */
async function checkRagStatus() {
  // RAG는 opt-in 기능이라 대부분의 배포에서 꺼져 있다 — 실패해도(네트워크 오류 등)
  // 조용히 넘어가고 버튼은 숨김 상태 그대로 둔다(로그인 리다이렉트를 유발하면 안 되므로
  // api() 대신 직접 fetch — /rag/status는 인증 없이도 404가 아니라 정상 응답해야 하지만,
  // 그래도 이 체크 자체가 로그인 흐름을 방해하지 않게 방어적으로 처리).
  try {
    const token = localStorage.getItem(TOKEN_KEY);
    const res = await fetch('/api/rag/status', {
      headers: token ? { 'Authorization': `Bearer ${token}` } : {},
    });
    if (!res.ok) return;
    const data = await res.json();
    ragEnabled = !!data.enabled;
    ragConfiguredProviders = data.configured_providers || [];
    ragIncludeProfile = !!data.include_profile;
  } catch (e) {
    ragEnabled = false;
    ragConfiguredProviders = [];
    ragIncludeProfile = false;
  }
  document.getElementById('rag-nav-btn')?.classList.toggle('hidden', !ragEnabled);
}

function stopInProgressPolling() {
  if (_inProgressTimer) { clearInterval(_inProgressTimer); _inProgressTimer = null; }
  document.getElementById('in-progress-banner')?.classList.add('hidden');
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
    await checkRagStatus();
    navigate('dashboard');
    startInProgressPolling();
  } catch (e) {
    errorEl.classList.remove('hidden');
  }
}

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  stopInProgressPolling();
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
    return `<div class="pinned-card" data-slug="${slug}" data-nav="detail">
      <button class="pin-card-btn" data-slug="${slug}" data-action="toggle-pin" title="핀 해제">📌</button>
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
    return `<tr class="company-row${isTerminated ? ' terminated-row' : ''}" data-slug="${slug}" data-nav="detail">
      <td><input type="checkbox" class="row-check" data-slug="${slug}" /></td>
      <td>
        <button class="pin-btn${isPinned ? ' active' : ''}" data-slug="${slug}" data-action="toggle-pin" title="${isPinned ? '핀 해제' : '즐겨찾기 추가'}">📌</button>
      </td>
      <td>
        <strong>${escHtml(fm.company_name)}</strong>
        ${fm.source_url ? `<br/><a href="${safeHref(fm.source_url)}" target="_blank" rel="noopener" style="font-size:11px;color:#4361ee;font-weight:600;">🔗 공고 원문</a>` : ''}
      </td>
      <td>${escHtml(fm.job_title || '-')}</td>
      <td><span class="score-badge ${scoreClass}">${scoreText}</span></td>
      <td>${escHtml(fm.fit_label || '-')}</td>
      <td>${escHtml(fm.stability || '-')}</td>
      <td>${escHtml(fm.location || '-')}</td>
      <td>
        <select class="status-select status-${status}" data-slug="${slug}" data-action="status-change">
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
    const result = await api(`/companies/${encodeURIComponent(slug)}/pin`, { method: 'POST', body: '{}' });
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

function onCheckChange(changedEl) {
  selectedSlugs = new Set(
    [...document.querySelectorAll('.row-check:checked')].map(el => el.dataset.slug)
  );
  if (selectedSlugs.size > 5) {
    if (changedEl) {
      changedEl.checked = false;
      selectedSlugs.delete(changedEl.dataset.slug);
    } else {
      document.querySelectorAll('.row-check:checked').forEach(c => { c.checked = false; });
      selectedSlugs.clear();
    }
    showToast('비교는 한 번에 최대 5개까지 선택할 수 있습니다.', 'error');
  }
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
    [...selectedSlugs].map(slug => api(`/companies/${encodeURIComponent(slug)}`, { method: 'DELETE' }))
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
    record = await api(`/companies/${encodeURIComponent(slug)}`);
  } catch (e) {
    selectEl.className = prevClass;
    selectEl.value = prevValue;
    showToast('상태 변경 실패: ' + e.message, 'error');
    return;
  }
  record.frontmatter.status = newStatus;

  // 지원 상태 로그 섹션에 날짜 자동 기록
  const today = localDateString();
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
    record = await api(`/companies/${encodeURIComponent(slug)}`);
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
  loadFitHistory(slug);

  // 메타 칩
  const chips = document.getElementById('meta-chips');
  if (chips) {
    const items = [fm.stability && `안정성: ${escHtml(fm.stability)}`, escHtml(fm.location), escHtml(fm.employee_count)].filter(Boolean);
    const statusSel = escHtml(fm.status);
    chips.innerHTML = items.map(i => `<span class="chip">${i}</span>`).join('') +
      `<select class="status-select status-${statusSel}" data-slug="${escHtml(currentSlug)}" data-action="status-change">
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
    // 충족 현황 테이블 열 너비 고정 + 2번째 칸(이모지+라벨) 이모지 뒤 줄바꿈, 라벨은 줄바꿈 없이
    for (const h3 of bodyEl.querySelectorAll('h3')) {
      if (h3.textContent.includes('충족 현황')) {
        let el = h3.nextElementSibling;
        while (el && el.tagName !== 'TABLE' && el.tagName !== 'H3') el = el.nextElementSibling;
        if (el?.tagName === 'TABLE') {
          el.classList.add('fit-check-table');
          el.querySelectorAll('tbody tr td:nth-child(2)').forEach(td => {
            const m = td.textContent.trim().match(/^(\S+)\s+(.+)$/);
            if (m) td.innerHTML = `${escHtml(m[1])}<br><span style="white-space:nowrap">${escHtml(m[2])}</span>`;
          });
        }
      }
    }
  }

  // 편집 폼 채우기
  fillEditForm(fm, record.body);
}

let _fitHistoryCache = [];

async function loadFitHistory(slug) {
  const toggleEl = document.getElementById('fit-history-toggle');
  const panelEl = document.getElementById('fit-history-panel');
  if (panelEl) { panelEl.classList.add('hidden'); panelEl.innerHTML = ''; }
  if (!toggleEl) return;
  try {
    _fitHistoryCache = await api(`/companies/${encodeURIComponent(slug)}/fit-history`);
  } catch (e) {
    console.error('평가 이력 로딩 실패:', e);
    _fitHistoryCache = [];
    // "이력 없음"과 구분해서 보여줌 — 안 보이면 사용자가 DB 문제를 알 방법이 없음
    toggleEl.textContent = '⚠ 평가 이력을 불러오지 못했습니다';
    toggleEl.classList.remove('hidden');
    return;
  }
  if (!_fitHistoryCache.length) {
    toggleEl.classList.add('hidden');
    return;
  }
  // 접었다 펼 필요 없이 늘 아래에 열려있게 — 버튼은 스크롤 이동 전용(실사용 피드백, 2026-08-21)
  toggleEl.textContent = `📋 평가 이력 보기 (${_fitHistoryCache.length}건)`;
  toggleEl.classList.remove('hidden');
  renderFitHistoryPanel();
  if (panelEl) panelEl.classList.remove('hidden');
}

function renderFitHistoryPanel() {
  const panelEl = document.getElementById('fit-history-panel');
  if (!panelEl) return;
  const rows = _fitHistoryCache.map((h, i) => {
    const prev = _fitHistoryCache[i + 1];  // 최신순이므로 다음 항목이 더 이전 값
    let delta = '';
    if (prev && typeof h.fit_score === 'number' && typeof prev.fit_score === 'number') {
      const diff = h.fit_score - prev.fit_score;
      if (diff !== 0) {
        delta = ` <span style="font-size:11px;color:${diff > 0 ? '#065f46' : '#991b1b'}">${diff > 0 ? '+' : ''}${diff}</span>`;
      }
    }
    let versionCell;
    if (!h.profile_version_id) versionCell = '<span style="color:#9ca3af">이전 버전 불명</span>';
    else if (!h.profile_version_created_at) versionCell = '<span style="color:#9ca3af">삭제됨</span>';
    else versionCell = `<a href="#" onclick="navigate('profile-version', '${h.profile_version_id}'); return false;">${escHtml(h.profile_version_created_at)}</a>`;
    return `<tr>
      <td><a href="#" onclick="navigate('fit-history', '${h.id}'); return false;">${escHtml(h.created_at)}</a></td>
      <td>${versionCell}</td>
      <td>${h.fit_score ?? '-'}${delta}</td>
      <td>${escHtml(h.fit_label || '')}</td>
    </tr>`;
  }).join('');

  panelEl.innerHTML = `
    <table class="info-table">
      <thead><tr><th>시점</th><th>프로필 버전</th><th>점수</th><th>라벨</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function toggleFitHistory() {
  if (!_fitHistoryCache.length) return;  // 로딩 실패(⚠) 상태 — 클릭해도 아무 반응 없게
  // 버튼은 상단(점수 배지 옆)인데 패널은 본문 아래라, 클릭해도 스크롤 안 하면
  // 화면엔 아무 변화가 안 보인다(실사용 중 발견, 2026-08-17) — 패널은 이제 항상 열려있으므로
  // 이 함수는 그 자리로 스크롤만 이동시킨다.
  document.getElementById('fit-history-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
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

async function renderQAHistory(slug) {
  const container = document.getElementById('qa-messages');
  if (!container) return;
  let messages;
  try {
    ({ messages } = await api(`/companies/${encodeURIComponent(slug)}/qa/history`));
  } catch (e) {
    // GET 실패 시 기존 화면(예: 방금 표시된 오류 말풍선)을 그대로 둔다 — DB 장애로 GET도
    // 실패하는 상황에서 컨테이너를 먼저 비우면 사용자가 아무 메시지도 못 본다(Codex 2차
    // 리뷰로 발견, 2026-08-22).
    console.error('QnA 히스토리 로딩 실패:', e);
    return;
  }
  // 응답 오는 동안 다른 회사로 이동했으면 그리지 않는다(오늘 다른 곳에서도 고친 것과
  // 같은 레이스 가드 — currentSlug를 다시 읽어 비교).
  if (currentSlug !== slug) return;
  container.innerHTML = '';
  messages.forEach(m => {
    appendBubble('qa-messages', m.question, 'user');
    if (m.status === 'done') {
      appendBubble('qa-messages', m.answer, 'assistant');
    } else if (m.status === 'pending') {
      appendBubble('qa-messages', '답변을 생성하고 있습니다. 최대 30~40초 정도 걸릴 수 있습니다...', 'assistant');
    } else {
      appendBubble('qa-messages', `오류: ${m.error || '응답 실패'}`, 'assistant');
    }
  });
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

  // 요청 시점의 slug를 고정 — API 호출이 끝나기 전에 사용자가 다른 화면으로 이동하면
  // currentSlug가 null이나 다른 값으로 바뀌어, 뒤늦게 도착한 응답이 initDetail(null) 등
  // 엉뚱한 slug로 화면 갱신을 시도해 가짜 "로딩 실패" 에러가 뜨는 문제가 있었음
  // (실사용 중 발견, 2026-08-21). 저장/재분석/재평가/동기화 4곳 전부 같은 패턴.
  const slug = currentSlug;
  try {
    await api(`/companies/${encodeURIComponent(slug)}`, {
      method: 'PUT',
      body: JSON.stringify({ frontmatter: fm, body }),
    });
    alert('저장되었습니다.');
    if (currentSlug === slug) { switchTab('info'); initDetail(slug); }
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

function toggleRefitDropdown(e) {
  e.stopPropagation();
  const panel = document.getElementById('refit-dropdown-panel');
  if (!panel) return;
  const isHidden = panel.classList.toggle('hidden');
  if (!isHidden) {
    const close = (ev) => {
      if (!document.getElementById('refit-dropdown-wrap')?.contains(ev.target)) {
        panel.classList.add('hidden');
        document.removeEventListener('click', close);
      }
    };
    document.addEventListener('click', close);
  }
}

async function refillCompany() {
  document.getElementById('refit-dropdown-panel')?.classList.add('hidden');
  if (!confirm('원문 기반으로 전체 재분석합니다. 기존 분석 내용이 덮어씌워집니다. 계속할까요?')) return;
  const btn = document.querySelector('#refit-dropdown-wrap .export-dropdown-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 재분석 중...'; }
  const slug = currentSlug;  // 요청 시점 고정 — 이유는 saveCompany() 주석 참고
  try {
    await api(`/companies/${encodeURIComponent(slug)}/refill`, { method: 'POST', body: '{}' });
    showToast('전체 재분석 완료!');
    // initDetail은 await 안 함 — 여기서 실패해도(존재하지 않는 loadDetail을 부르던
    // 버그가 있었음, 2026-08-18 발견) 재분석 자체는 이미 성공했으니 아래 catch에서
    // "재분석 실패"로 잘못 표시되면 안 됨(refitCompany()와 동일 패턴)
    if (currentSlug === slug) initDetail(slug);
  } catch (e) {
    showToast('재분석 실패: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🎯 재분석 ▾'; }
  }
}

async function refitCompany() {
  document.getElementById('refit-dropdown-panel')?.classList.add('hidden');
  const btn = document.querySelector('#refit-dropdown-wrap .export-dropdown-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 재평가 중...'; }
  const slug = currentSlug;
  try {
    await api(`/companies/${encodeURIComponent(slug)}/refit`, { method: 'POST', body: '{}' });
    showToast('적합도 재평가 완료!');
    if (currentSlug === slug) initDetail(slug);
  } catch (e) {
    showToast('재평가 실패: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🎯 재분석 ▾'; }
  }
}

async function syncWanted() {
  const form = document.getElementById('edit-form');
  const sourceUrl = form?.elements['source_url']?.value?.trim() || '';
  if (sourceUrl && !sourceUrl.includes('wanted.co.kr')) {
    alert('원티드 URL만 지원합니다. (wanted.co.kr)');
    return;
  }
  const slug = currentSlug;
  try {
    const body = sourceUrl ? JSON.stringify({ source_url: sourceUrl }) : '{}';
    const result = await api(`/companies/${encodeURIComponent(slug)}/sync-wanted`, { method: 'POST', body });
    const updated = Object.keys(result.updated || {}).join(', ');
    alert(`원티드 동기화 완료!\n업데이트된 항목: ${updated || '없음'}`);
    if (currentSlug === slug) { initDetail(slug); switchTab('edit'); }
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

  // 서버가 진실 공급원이라 로컬 배열에 push/pop할 필요가 없다 — pending 삽입과
  // done/failed 마킹을 서버가 다 처리한다(backend/routers/qa.py의 company_qa()).
  const slug = currentSlug;
  appendBubble('qa-messages', question, 'user');
  const assistantBubble = appendBubble('qa-messages', '', 'assistant');

  const token = localStorage.getItem(TOKEN_KEY);
  const makeFetch = () => fetch(`/api/companies/${encodeURIComponent(slug)}/qa`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
    body: JSON.stringify({ question }),
  });

  try {
    const fullText = await streamQA(makeFetch, assistantBubble);
    // fullText가 null이면 연결이 끊긴 것 — 서버는 독립 태스크로 계속 처리 중일 수 있으니
    // (재시도로 새 질문을 또 보내는 대신) 최신 상태를 서버에서 다시 조회해 반영한다.
    if (fullText === null && currentSlug === slug) await renderQAHistory(slug);
  } catch (e) {
    assistantBubble.textContent = `오류: ${e.message}`;
  }
}

async function sendCompareQA() {
  const input = document.getElementById('compare-qa-input');
  const question = input.value.trim();
  if (!question) return;
  input.value = '';

  const history = compareQaHistory.slice(-40);
  compareQaHistory.push({ role: 'user', text: question });
  appendBubble('compare-qa-messages', question, 'user');
  const assistantBubble = appendBubble('compare-qa-messages', '', 'assistant');

  const token = localStorage.getItem(TOKEN_KEY);
  const makeFetch = () => fetch('/api/companies/qa', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
    body: JSON.stringify({ slugs: compareTargets, question, history }),
  });

  try {
    const fullText = await streamQA(makeFetch, assistantBubble);
    if (fullText) compareQaHistory.push({ role: 'assistant', text: fullText });
    else compareQaHistory.pop(); // 응답 실패 시 방금 넣은 질문 롤백(sendQA()와 동일 이유)
  } catch (e) {
    assistantBubble.textContent = `오류: ${e.message}`;
    compareQaHistory.pop();
  }
}

function appendBubble(containerId, text, role) {
  const container = document.getElementById(containerId);
  const bubble = document.createElement('div');
  bubble.className = role === 'assistant' ? 'qa-bubble assistant markdown-body' : 'qa-bubble user';
  if (role === 'assistant' && text) bubble.innerHTML = parseMarkdown(text);
  else bubble.textContent = text;
  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
  return bubble;
}

async function streamQA(fetchFn, bubble) {
  // 예전엔 연결이 끊기면 같은 질문으로 POST를 최대 2번 자동 재시도했는데, 서버 저장 전환
  // 이후엔 POST 한 번마다 새 행+새 LLM 호출이 생겨서 재시도가 이력·비용 중복을 만들었다
  // (Codex 리뷰로 발견, 2026-08-22). 서버가 pending 상태를 이미 들고 있어서(연결이 끊겨도
  // 독립 태스크가 계속 처리) 재시도 없이 한 번만 시도하고, 실패하면 호출부가 서버 상태를
  // 다시 조회하도록 한다 — sendQA()의 history 재조회 참고.
  try {
    const res = await fetchFn();
    if (!res.ok) {
      // non-OK(예: DB 장애로 503)는 서버가 요청을 아예 안 받아준 것 — 이미 말풍선에 오류를
      // 표시했으니 호출부가 history를 다시 조회할 필요가 없다. undefined를 반환해 연결
      // 단절(null, 서버가 독립 태스크로 처리 중일 수 있어 재조회가 의미 있음)과 구분한다
      // (Codex 2차 리뷰로 발견, 2026-08-22 — 재조회가 컨테이너를 비웠다가 같은 장애로
      // 실패하면 방금 표시한 오류까지 같이 사라짐).
      const err = await res.json().catch(() => ({ detail: '서버 오류' }));
      bubble.textContent = `오류: ${err.detail || '응답 실패'}`;
      return undefined;
    }
    bubble.textContent = '';
    return await consumeSSE(res, bubble);
  } catch (e) {
    bubble.textContent = '연결이 끊겼습니다.';
    return null;
  }
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
          const { text, error, message_id } = JSON.parse(payload);
          // company_qa()가 스트리밍 첫 이벤트로 message_id를 보낸다(text/error 없음) —
          // 지금은 화면에서 안 쓰지만, text가 undefined인 채로 fullText에 이어붙으면
          // "undefined" 문자열이 답변 앞에 섞여 들어간다.
          if (message_id !== undefined) continue;
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
  // 여기 도달했다는 건 [DONE]을 못 보고 루프가 끝났다는 뜻(연결이 중간에 조용히 끊김,
  // AbortError로 취소됨 등) — fullText가 비어있지 않아도 완결된 응답이 아니므로 null을
  // 반환해 "확정 성공"과 구분한다. 예전엔 여기서 fullText를 그대로 반환해서, 부분 텍스트가
  // 있으면 sendCompareQA()의 `if (fullText) ...`가 잘린 응답을 성공으로 착각해 히스토리에
  // 그대로 남기는 문제가 있었다(Codex 리뷰 대응 중 발견, 2026-08-22).
  return null;
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
  compareQaHistory = [];
  let records;
  try {
    const params = new URLSearchParams();
    slugs.forEach(s => params.append('slugs', s));
    records = await api(`/companies/compare?${params}`);
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
  initModelTierHover();

  // 추가 설명 복원 (서버 저장값 — 마지막 업로드 때 입력한 내용이 다음 업로드에도 남음)
  // 한 번도 입력한 적 없으면 비워둔다 — 안내는 placeholder 속성(미리보기 힌트)이 담당,
  // 실제 값에 틀을 채워넣으면 이미 입력한 것처럼 보여서 혼동을 줌(2026-08-20 지적).
  try {
    const noteData = await api('/profile/note');
    const extraNoteEl = document.getElementById('profile-extra-note');
    if (extraNoteEl) extraNoteEl.value = noteData.text || '';
  } catch (e) { console.error('프로필 추가 설명 로딩 실패:', e); }

  ['claude-high-model', 'claude-light-model', 'openai-high-model', 'openai-light-model', 'gemini-high-model', 'gemini-light-model'].forEach(id => {
    const el = document.getElementById(id);
    const key = id.replace(/-/g, '_');
    if (el) el.value = currentSettings[key] || '';
  });

  ['notify-strengths', 'notify-gaps', 'notify-jobplanet-rating', 'notify-employee-count', 'notify-weekly-summary'].forEach(id => {
    const el = document.getElementById(id);
    const key = id.replace(/-/g, '_');
    if (el) el.checked = !!currentSettings[key];
  });

  const weekdaySelect = document.getElementById('weekly-summary-weekday');
  if (weekdaySelect) weekdaySelect.value = String(currentSettings.weekly_summary_weekday ?? 0);
  const timeInput = document.getElementById('weekly-summary-time');
  if (timeInput) timeInput.value = currentSettings.weekly_summary_time || '09:00';

  try {
    const criteriaData = await api('/eval-criteria');
    const criteriaEl = document.getElementById('eval-criteria-input');
    if (criteriaEl) criteriaEl.value = criteriaData.text || '';
  } catch (e) { console.error('평가 기준 로딩 실패:', e); }

  try {
    const usageData = await api('/usage');
    renderUsage(usageData);
  } catch (e) { console.error('사용량 로딩 실패:', e); }

  loadProfileVersions();
}

async function loadProfileVersions() {
  const listEl = document.getElementById('profile-version-list');
  const titleEl = document.getElementById('profile-version-title');
  if (!listEl) return;
  let versions;
  try {
    versions = await api('/profile/versions');
  } catch (e) {
    console.error('프로필 버전 목록 로딩 실패:', e);
    // "아직 없음"과 구분해서 보여줌 — 안 보이면 사용자가 DB 문제를 알 방법이 없음
    listEl.innerHTML = '<p style="font-size:13px;color:#dc2626">⚠ 이전 버전 목록을 불러오지 못했습니다.</p>';
    return;
  }
  if (titleEl) titleEl.textContent = `이전 버전 (${versions.length}개)`;
  if (!versions.length) {
    listEl.innerHTML = '<p style="font-size:13px;color:#9ca3af">아직 이전 버전이 없습니다.</p>';
    return;
  }
  _profileVersionsCache = versions;
  listEl.innerHTML = versions.map(v => `
    <div class="profile-version-row" style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid #f0f0f0">
      <div data-action="edit-version-note" data-id="${v.id}" style="min-width:0;overflow:hidden;cursor:pointer">
        <div style="font-size:13px">${v.created_at}</div>
        <div style="font-size:12px;color:${v.note ? '#6b7280' : '#c1c5cd'};white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${v.note ? escHtml(v.note) : '+ 메모 추가'}</div>
      </div>
      <div style="display:flex;gap:6px;flex-shrink:0">
        <button class="btn-secondary" onclick="navigate('profile-version', '${v.id}')" style="font-size:12px;padding:5px 10px;white-space:nowrap">보기</button>
        <button class="btn-secondary" onclick="deleteProfileVersionUI(${v.id})" style="font-size:12px;padding:5px 8px;white-space:nowrap">🗑</button>
      </div>
    </div>
  `).join('');
}

let _profileVersionsCache = [];

async function editProfileVersionNote(id) {
  const v = _profileVersionsCache.find(x => x.id === id);
  const note = prompt('메모', (v && v.note) || '');
  if (note === null) return;  // 취소
  try {
    await api(`/profile/versions/${id}`, { method: 'PATCH', body: JSON.stringify({ note }) });
    loadProfileVersions();
  } catch (e) {
    showToast('메모 저장 실패: ' + e.message, 'error');
  }
}

async function deleteProfileVersionUI(id) {
  if (!confirm('이 버전을 삭제하시겠습니까?')) return;
  try {
    await api(`/profile/versions/${id}`, { method: 'DELETE' });
    showToast('삭제되었습니다.', 'success');
    loadProfileVersions();
  } catch (e) {
    showToast('삭제 실패: ' + e.message, 'error');
  }
}

async function initProfileVersion(versionId) {
  const metaEl = document.getElementById('profile-version-detail-meta');
  const bodyEl = document.getElementById('profile-version-detail-body');
  if (!metaEl || !bodyEl) return;
  let record;
  try {
    record = await api(`/profile/versions/${versionId}`);
  } catch (e) {
    metaEl.textContent = '';
    bodyEl.innerHTML = `<p style="color:#9ca3af">불러오지 못했습니다: ${escHtml(e.message)}</p>`;
    return;
  }
  const fm = record.frontmatter || {};
  metaEl.textContent = `저장 시점: ${fm.updated_at || ''}` + (record.note ? ` · 메모: ${record.note}` : '');
  if (record.body && record.body.trim()) {
    bodyEl.innerHTML = parseMarkdown(record.body);
    applyHljs(bodyEl);
  } else {
    bodyEl.innerHTML = '<p style="color:#9ca3af">내용이 없습니다.</p>';
  }
}

async function initFitHistoryDetail(entryId) {
  const metaEl = document.getElementById('fit-history-detail-meta');
  const bodyEl = document.getElementById('fit-history-detail-body');
  const backBtn = document.getElementById('fit-history-back-btn');
  if (!metaEl || !bodyEl) return;
  let record;
  try {
    record = await api(`/fit-history/${entryId}`);
  } catch (e) {
    metaEl.textContent = '';
    bodyEl.innerHTML = `<p style="color:#9ca3af">불러오지 못했습니다: ${escHtml(e.message)}</p>`;
    return;
  }
  metaEl.textContent = `평가 시점: ${record.history_created_at || ''}`;
  if (backBtn && record.slug) {
    backBtn.onclick = () => navigate('detail', record.slug);
  }
  if (record.body && record.body.trim()) {
    bodyEl.innerHTML = parseMarkdown(record.body);
    applyHljs(bodyEl);
  } else {
    bodyEl.innerHTML = '<p style="color:#9ca3af">내용이 없습니다.</p>';
  }
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
    loadProfileVersions();
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
  const maxTokens = parseInt(document.getElementById('profile-max-tokens')?.value || '16384', 10);
  formData.append('max_tokens', String(maxTokens));
  const versionNote = document.getElementById('profile-upload-version-note')?.value.trim() || '';
  if (versionNote) formData.append('version_note', versionNote);

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
  document.getElementById('model-inputs-gemini').style.display = provider === 'gemini' ? '' : 'none';
}

// 알려진 모델 입력 단가 ($/1M tokens) — 미등록 모델은 이름 패턴으로 추정
const MODEL_INPUT_PRICE = {
  // Claude
  'claude-opus': 5, 'claude-sonnet': 3, 'claude-haiku': 1,
  // GPT-5 (구체적 패턴 먼저)
  'gpt-5.5-pro': 30, 'gpt-5.5': 5,
  'gpt-5.4-pro': 30, 'gpt-5.4-mini': 0.75, 'gpt-5.4-nano': 0.2, 'gpt-5.4': 2.5,
  'gpt-5.2-pro': 21, 'gpt-5.2': 1.75,
  'gpt-5.1': 1.25,
  'gpt-5-pro': 15, 'gpt-5-mini': 0.25, 'gpt-5-nano': 0.05, 'gpt-5': 1.25,
  // GPT-4.1
  'gpt-4.1-mini': 0.4, 'gpt-4.1-nano': 0.1, 'gpt-4.1': 2,
  // GPT-4o
  'gpt-4o-mini': 0.15, 'gpt-4o': 2.5,
  // GPT-4 legacy
  'gpt-4-turbo': 10, 'gpt-4-32k': 60, 'gpt-4': 30,
  // GPT-3.5
  'gpt-3.5-turbo-16k': 3, 'gpt-3.5-turbo': 0.5,
  // o-series (구체적 패턴 먼저)
  'o1-pro': 150, 'o1-mini': 1.1, 'o1': 15,
  'o3-pro': 20, 'o3-mini': 1.1, 'o3': 2,
  'o4-mini': 1.1,
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

function initModelTierHover() {
  const wrap = document.querySelector('.model-tier-wrap');
  const panel = document.getElementById('model-tier-view');
  if (!wrap || !panel) return;
  let hideTimer = null;
  const show = () => { clearTimeout(hideTimer); panel.classList.add('visible'); };
  const hide = () => { hideTimer = setTimeout(() => panel.classList.remove('visible'), 150); };
  wrap.addEventListener('mouseenter', show);
  wrap.addEventListener('mouseleave', hide);
  panel.addEventListener('mouseenter', show);
  panel.addEventListener('mouseleave', hide);
}

function renderModelTierView(models, provider) {
  const tierView = document.getElementById('model-tier-view');
  if (!tierView) return;
  const sorted = [...models].sort((a, b) => (getModelPrice(b) ?? 0) - (getModelPrice(a) ?? 0));
  tierView.innerHTML = '<div style="font-size:12px;color:var(--text-muted);margin-bottom:8px;font-weight:600">비용 순서 (비쌈 → 저렴)</div>'
    + sorted.map(m => {
      const price = getModelPrice(m);
      const { bar, label } = getTierStyle(price);
      const tooltip = price !== null ? `입력 $${price}/1M tokens` : '단가 정보 없음';
      return `<div title="${tooltip}" style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--border);cursor:default">`
        + `<span style="width:4px;height:20px;border-radius:2px;background:${bar};flex-shrink:0"></span>`
        + `<span style="font-size:13px;color:var(--text-primary);flex:1">${escHtml(m)}</span>`
        + `<span style="font-size:11px;color:var(--text-muted);white-space:nowrap">${label}</span>`
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
    gemini_high_model: document.getElementById('gemini-high-model')?.value || null,
    gemini_light_model: document.getElementById('gemini-light-model')?.value || null,
    notify_strengths: document.getElementById('notify-strengths')?.checked ?? null,
    notify_gaps: document.getElementById('notify-gaps')?.checked ?? null,
    notify_jobplanet_rating: document.getElementById('notify-jobplanet-rating')?.checked ?? null,
    notify_employee_count: document.getElementById('notify-employee-count')?.checked ?? null,
    notify_weekly_summary: document.getElementById('notify-weekly-summary')?.checked ?? null,
    weekly_summary_weekday: document.getElementById('weekly-summary-weekday') ? parseInt(document.getElementById('weekly-summary-weekday').value, 10) : null,
    weekly_summary_time: document.getElementById('weekly-summary-time')?.value || null,
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
    document.addEventListener('click', close);
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
    a.download = `job-fitcheck_backup_${localDateString()}.zip`;
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
    a.download = `companies_${localDateString()}.csv`;
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

/* ── RAG (opt-in, 6번 항목) ───────────────────────────────────────── */
const RAG_LEVEL_CLASS = { '직접 근거': 'score-high', '부분 근거': 'score-mid', '인접 경험': 'score-mid', '근거 없음': 'score-none' };
const RAG_CHATS_KEY = 'job-fitcheck-rag-chats';
const RAG_CURRENT_CHAT_KEY = 'job-fitcheck-rag-current-chat';

function ragSettingsProviderOptionsHtml(current) {
  // rag_configured_providers 기반 — local 미설정 배포에는 Local 선택지 자체가 안 보인다(3번 결정).
  // "자동"은 메인 LLM provider를 따라간다(config.py의 resolve_rag_embedding_provider()).
  const auto = `<option value=""${current ? '' : ' selected'}>자동 (메인 provider 따름)</option>`;
  const opts = ragConfiguredProviders
    .map(p => `<option value="${p}"${p === current ? ' selected' : ''}>${p === 'google' ? 'Google' : 'Local'}</option>`)
    .join('');
  return auto + opts;
}

async function toggleRagSettings() {
  const popup = document.getElementById('rag-settings-popup');
  const willShow = popup.classList.contains('hidden');
  popup.classList.toggle('hidden');
  if (willShow) await loadRagSettings();
}

async function loadRagSettings() {
  const select = document.getElementById('rag-settings-provider-select');
  const note = document.getElementById('rag-settings-note');
  try {
    const data = await api('/rag/settings');
    select.innerHTML = ragSettingsProviderOptionsHtml(data.override);
    select.dataset.previousValue = data.override || '';
    note.textContent = `현재 적용: ${data.resolved}`;
  } catch (e) {
    note.innerHTML = `<span class="rag-error">${escHtml(e.message)}</span>`;
  }
}

async function saveRagSettings() {
  const select = document.getElementById('rag-settings-provider-select');
  const note = document.getElementById('rag-settings-note');
  const previous = select.dataset.previousValue || '';
  if (select.value === previous) return;  // 실질적 변경 없음
  if (!confirm('이 provider로 전환하려면 먼저 재색인이 필요합니다(API 호출 비용이 발생할 수 있습니다). 지금 진행할까요?')) {
    select.value = previous;
    return;
  }
  select.disabled = true;
  note.textContent = '재색인 중... (몇 초~몇 분 걸릴 수 있습니다)';
  try {
    const data = await api('/rag/settings', {
      method: 'PUT',
      body: JSON.stringify({ embedding_provider: select.value || null }),
    });
    select.dataset.previousValue = select.value;
    note.textContent = `현재 적용: ${data.resolved}`;
  } catch (e) {
    select.value = previous;  // 실패 시 이전 선택으로 되돌림 — override는 서버에서도 그대로 유지됨
    note.innerHTML = `<span class="rag-error">${escHtml(e.message)}</span>`;
  } finally {
    select.disabled = false;
  }
}

let _ragNavHeightListenerAttached = false;

function updateRagNavHeight() {
  const nav = document.querySelector('.navbar');
  if (nav) document.documentElement.style.setProperty('--rag-nav-h', `${nav.offsetHeight}px`);
}

async function initRag() {
  document.getElementById('rag-gap-section').classList.toggle('hidden', !ragIncludeProfile);
  document.getElementById('rag-gap-disabled-note').classList.toggle('hidden', ragIncludeProfile);
  document.getElementById('rag-question-input').addEventListener('keydown', handleRagKeydown);
  // 좀비 pending 정리(ragCleanupPendingMessages)는 더 이상 필요 없다 — 서버가 진실
  // 공급원이라 pending은 서버 재시작 시점에만 확정적으로 정리되고(app_db.py의
  // _fail_stale_rag_pending), 클라이언트가 시간 추측으로 지울 이유가 없다.
  await ragRenderChatDropdown();
  await ragSwitchChat(ragGetCurrentChatId());

  // nav 실제 높이(모바일에서 줄바꿈되면 가변)를 측정해 .rag-view의 높이 계산에 반영
  updateRagNavHeight();
  if (!_ragNavHeightListenerAttached) {
    window.addEventListener('resize', updateRagNavHeight);
    _ragNavHeightListenerAttached = true;
  }
}

function handleRagKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    e.target.closest('form').requestSubmit();
  }
}

async function runRagReindex() {
  if (!confirm('공고/프로필 변경사항을 임베딩에 반영합니다. API 호출 비용이 발생할 수 있습니다. 계속할까요?')) return;
  const btn = document.getElementById('rag-reindex-btn');
  const statusEl = document.getElementById('rag-reindex-status');
  btn.disabled = true;
  btn.textContent = '재색인 중...';
  statusEl.textContent = '';
  try {
    const data = await api('/rag/reindex', { method: 'POST' });
    statusEl.textContent = `재색인 완료 (${data.provider})`;
  } catch (e) {
    statusEl.innerHTML = `<span class="rag-error">${escHtml(e.message)}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '🔄 재색인';
  }
}

async function runRagGapCheck(event) {
  event.preventDefault();
  const skill = document.getElementById('rag-skill-input').value.trim();
  const btn = document.getElementById('rag-gap-submit-btn');
  const resultEl = document.getElementById('rag-gap-result');
  if (!skill) return;

  btn.disabled = true;
  btn.textContent = '확인 중...';
  resultEl.innerHTML = '';
  try {
    const data = await api('/rag/gap-check', { method: 'POST', body: JSON.stringify({ skill }) });
    resultEl.innerHTML = renderRagGapCard(data);
  } catch (e) {
    resultEl.innerHTML = `<div class="rag-error">${escHtml(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '확인';
  }
}

function renderRagGapCard(data) {
  const badgeClass = RAG_LEVEL_CLASS[data.evidence_level] || 'score-none';
  const demand = data.market_demand;
  let html = `
    <h4 style="margin-top:14px">${escHtml(data.skill)} <span class="score-badge ${badgeClass}">${escHtml(data.evidence_level)}</span></h4>
    <div class="rag-result-section">
      <h4>시장 수요${demand.method === 'exact' ? '' : ' <span class="score-badge score-mid">추정치</span>'}</h4>
      <p>${demand.method === 'exact'
        ? `전체 공고 ${demand.total}건 중 ${demand.matched}건 요구 (${(demand.ratio * 100).toFixed(1)}%)`
        : `약 ${demand.matched}건 / 전체 ${demand.total}건 (${(demand.ratio * 100).toFixed(1)}%) — 후보 ${demand.candidate_count ?? '?'}건 중 LLM 판정 추정치`}</p>
    </div>
    <div class="rag-result-section">
      <h4>판정 근거</h4>
      <div class="markdown-body">${parseMarkdown(data.reasoning)}</div>
    </div>
  `;
  if (data.excerpts && data.excerpts.length) {
    html += `<div class="rag-result-section"><h4>검색된 프로필 발췌문 (${data.excerpts.length}건)</h4>`;
    for (const e of data.excerpts) html += `<div class="rag-excerpt markdown-body">${parseMarkdown(e)}</div>`;
    html += `</div>`;
  }
  if (data.action_plan) {
    const ap = data.action_plan;
    html += `
      <div class="rag-result-section">
        <h4>행동 계획</h4>
        <p><strong>활동:</strong></p><div class="markdown-body">${parseMarkdown(ap.activity)}</div>
        <p><strong>남길 증거:</strong></p><div class="markdown-body">${parseMarkdown(ap.evidence_to_produce)}</div>
        <p><strong>완료 조건:</strong></p><div class="markdown-body">${parseMarkdown(ap.completion_criteria)}</div>
      </div>
    `;
  }
  html += `<p class="rag-note">provider: ${escHtml(data.provider)}</p>`;
  return html;
}

/* ── RAG 채팅(멀티세션, Agent) ────────────────────────────────────── */
// 채팅 데이터(방 목록·메시지)는 서버(rag_chats/rag_messages)가 진실 공급원 — 로컬에는
// "지금 어느 방을 보고 있는지"만(UI 상태, 데이터 아님) 남긴다.
function ragGetCurrentChatId() { return localStorage.getItem(RAG_CURRENT_CHAT_KEY); }
function ragSetCurrentChatId(id) { localStorage.setItem(RAG_CURRENT_CHAT_KEY, id); }

// localStorage의 옛 job-fitcheck-rag-chats는 1회성 마이그레이션 소스로만 씀
// (migrateRagChatsIfNeeded 참고).
async function migrateRagChatsIfNeeded() {
  if (localStorage.getItem('job-fitcheck-rag-chats-migrated') === '1') return;
  const raw = localStorage.getItem(RAG_CHATS_KEY);
  const oldChats = raw ? JSON.parse(raw) : {};
  if (Object.keys(oldChats).length === 0) {
    localStorage.setItem('job-fitcheck-rag-chats-migrated', '1');
    return;
  }
  const chats = {};
  for (const [id, chat] of Object.entries(oldChats)) {
    chats[id] = {
      title: chat.title || null,
      created_at_ms: chat.createdAt,
      messages: (chat.messages || []).map(m => ({ question: m.question, data: m.data, pending: !!m.pending })),
    };
  }
  try {
    await api('/rag/migrate-chats', { method: 'POST', body: JSON.stringify({ chats }) });
    localStorage.setItem('job-fitcheck-rag-chats-migrated', '1');
  } catch (e) {
    console.warn('RAG 채팅 마이그레이션 실패, 다음 로드 때 재시도:', e);
  }
}

async function createNewRagChat() {
  const chat = await api('/rag/chats', { method: 'POST' });
  ragSetCurrentChatId(chat.id);
  document.getElementById('rag-question-input').value = '';
  await ragRenderChatDropdown();
  ragRenderThread([]);
}

async function deleteCurrentRagChat() {
  const currentId = ragGetCurrentChatId();
  if (!currentId) return;
  if (!confirm('이 채팅을 삭제하시겠습니까?')) return;
  await api(`/rag/chats/${encodeURIComponent(currentId)}`, { method: 'DELETE' });
  localStorage.removeItem(RAG_CURRENT_CHAT_KEY);
  await ragRenderChatDropdown();
  await ragSwitchChat(ragGetCurrentChatId());
}

async function ragRenderChatDropdown() {
  const { chats } = await api('/rag/chats');
  const select = document.getElementById('rag-chat-select');
  if (!select) return;
  if (chats.length === 0) { await createNewRagChat(); return; }
  let current = ragGetCurrentChatId();
  if (!current || !chats.some(c => c.id === current)) {
    current = chats[0].id;
    ragSetCurrentChatId(current);
  }
  select.innerHTML = chats.map(c => {
    const title = c.title || '(새 채팅)';
    return `<option value="${c.id}" ${c.id === current ? 'selected' : ''}>${escHtml(title)}</option>`;
  }).join('');
}

async function ragSwitchChat(chatId) {
  ragSetCurrentChatId(chatId);
  if (!chatId) return;
  try {
    const { messages } = await api(`/rag/chats/${encodeURIComponent(chatId)}`);
    // 조회하는 동안 사용자가 다른 방으로 옮겨갔으면 엉뚱한 화면에 덮어쓰지 않는다
    // (오늘 다른 곳에서도 고친 것과 같은 레이스 가드).
    if (ragGetCurrentChatId() === chatId) ragRenderThread(messages);
  } catch (e) {
    console.error('RAG 채팅 로딩 실패:', e);
  }
}

function ragRenderThread(messages) {
  const resultEl = document.getElementById('rag-ask-result');
  if (!resultEl) return;
  resultEl.innerHTML = messages.map(m => {
    let body;
    if (m.status === 'pending') body = '답변을 생성하고 있습니다. 최대 30~40초 정도 걸릴 수 있습니다...';
    else if (m.status === 'failed') body = `<div class="rag-error">오류: ${escHtml(m.error || '응답 실패')}</div>`;
    else body = renderRagAskAnswer(m.data);
    return `
    <div class="qa-bubble user">${escHtml(m.question)}</div>
    <div class="qa-bubble assistant">${body}</div>
  `;
  }).join('');
  resultEl.scrollTop = resultEl.scrollHeight;
}

async function runRagAsk(event) {
  event.preventDefault();
  const question = document.getElementById('rag-question-input').value.trim();
  const btn = document.getElementById('rag-ask-submit-btn');
  if (!question) return;

  const chatId = ragGetCurrentChatId();  // 응답 도착 시 사용자가 다른 채팅으로 옮겨가 있어도
  if (!chatId) return;                   // 엉뚱한 화면에 덮어쓰지 않기 위해 요청 시점에 고정

  document.getElementById('rag-question-input').value = '';
  btn.disabled = true;
  btn.textContent = '전송 중...';

  // 서버가 pending 행을 이미 저장해두므로(응답 기다리는 동안 화면을 나갔다 와도 GET으로
  // 최신 상태가 그대로 보임), 여기서는 즉시 보여줄 pending 말풍선만 낙관적으로 붙인다.
  const resultEl = document.getElementById('rag-ask-result');
  if (resultEl) {
    resultEl.innerHTML += `
      <div class="qa-bubble user">${escHtml(question)}</div>
      <div class="qa-bubble assistant">답변을 생성하고 있습니다. 최대 30~40초 정도 걸릴 수 있습니다...</div>
    `;
    resultEl.scrollTop = resultEl.scrollHeight;
  }

  try {
    await api('/rag/ask', { method: 'POST', body: JSON.stringify({ question, chat_id: chatId }) });
  } catch (e) {
    // 실패해도 서버에 status='failed'로 남아있으니 별도 처리 없이 아래에서 최신 상태를 다시 조회
  } finally {
    if (ragGetCurrentChatId() === chatId) await ragSwitchChat(chatId);  // 서버 최신 상태로 갱신
    await ragRenderChatDropdown();  // 첫 질문이었다면 제목이 방금 채워졌을 수 있음
    btn.disabled = false;
    btn.textContent = '전송';
  }
}

function renderRagAskAnswer(data) {
  let inner = `<div class="markdown-body">${parseMarkdown(data.answer || '(응답 없음)')}</div>`;
  if (data.tool_calls && data.tool_calls.length) {
    inner += `<div class="rag-result-section"><h4>사용한 도구 (${data.tool_calls.length}건)</h4>`;
    for (const tc of data.tool_calls) {
      inner += `<details class="rag-tool-trace"><summary>${escHtml(tc.tool)}(${escHtml(JSON.stringify(tc.args))})</summary><pre class="rag-tool-result">${escHtml(JSON.stringify(tc.result, null, 2))}</pre></details>`;
    }
    inner += `</div>`;
  }
  inner += `<p class="rag-note">임베딩 provider: ${escHtml(data.provider)}</p>`;
  return inner;
}

/* ── 초기 로드 ────────────────────────────────────────────────────── */
window.addEventListener('DOMContentLoaded', async () => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) {
    currentView = 'login';
    currentSlug = null;
    history.replaceState({ view: 'login', slug: null, compareTargets: [] }, '', window.location.pathname);
    render();
    return;
  }
  migrateQAHistoryIfNeeded();  // fire-and-forget — 렌더링을 막을 이유 없음
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
  // /rag로 직접 진입(새로고침 등)했을 때 render()가 checkRagStatus() 응답보다 먼저 끝나면
  // initRag()가 초기값(ragEnabled=false 등)으로 UI를 그리고, checkRagStatus()는 나중에
  // 값을 받아와도 nav 버튼 외엔 다시 안 그려서 그 상태로 굳어버린다(코드리뷰 6번, 2026-07-31
  // Playwright로 재현 확인). render() 전에 기다려서 애초에 잘못 그릴 일을 없앤다.
  await checkRagStatus();
  if (ragEnabled) migrateRagChatsIfNeeded();  // fire-and-forget, RAG 꺼져있으면 503이라 가드
  // RAG가 비활성인 배포에서 토큰을 가진 사용자가 /rag를 직접 열면(새로고침·북마크 등) nav
  // 버튼은 숨어도 뷰 자체는 그려져서, 뭘 눌러도 503만 나는 깨진 화면이 뜬다(코드리뷰 5번,
  // 2026-08-02) — ragEnabled를 알고 난 뒤 대시보드로 돌려보낸다.
  if (currentView === 'rag' && !ragEnabled) {
    currentView = 'dashboard';
    history.replaceState({ view: 'dashboard', slug: null, compareTargets: [] }, '', '/');
  }
  render();
  startInProgressPolling();
});
