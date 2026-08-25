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
