"""회사 정보 기반 Q&A — SSE 스트리밍."""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

import prompts
import storage
from llm.router import high_provider
from models import MultiQARequest, QAMessage, QAMigrationRequest, QARequest
from services.app_db import insert_pending_qa, list_fit_history, list_qa_context, list_qa_history, mark_qa_done, mark_qa_failed

router = APIRouter()
logger = logging.getLogger(__name__)

# 진행 중인 QnA 생성 태스크 참조 보관 — asyncio 문서 권고대로, 참조를 안 들고 있으면
# 이벤트 루프가 GC 시점에 실행 중인 태스크를 조용히 없애버릴 수 있다.
_active_qa_tasks: set[asyncio.Task] = set()


def _fit_history_summary(slug: str) -> str:
    """이 회사의 시점별 적합도 점수 변화 요약. QnA는 현재 스냅샷만 컨텍스트로 받다 보니
    "이전엔 몇 점이었는지"를 답할 근거가 아예 없었음(실사용 중 발견, 2026-08-19) — 회사
    상세 화면에도 이미 쓰는 fit_history를 그대로 재사용해 채워준다. 이력 DB 조회 실패는
    QnA 핵심 기능(질문 응답)을 막으면 안 되므로 조용히 빈 문자열로 넘어간다."""
    try:
        entries = list_fit_history(slug)
    except Exception:
        return ""
    if len(entries) < 2:
        return ""  # 평가가 한 번뿐이면 "변화"랄 게 없어 굳이 안 보여줌
    entries = sorted(entries, key=lambda e: e["id"])
    lines = []
    prev_score = None
    for e in entries:
        score = e["fit_score"]
        delta = f" ({'+' if score - prev_score >= 0 else ''}{score - prev_score})" if prev_score is not None else ""
        lines.append(f"- {e['created_at']}: {score}점 {e['fit_label']}{delta}")
        prev_score = score
    return "## 적합도 평가 이력 (시점별 점수 변화)\n" + "\n".join(lines) + "\n\n"


def _make_sse(gen):
    """AsyncIterator를 SSE(text/event-stream) StreamingResponse로 변환. 다중 회사 비교
    QnA(multi_company_qa)는 서버 저장 대상이 아니라 지금도 이 방식 그대로 쓴다."""
    async def _wrapped():
        try:
            async for chunk in gen:
                yield f"data: {json.dumps({'text': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(_wrapped(), media_type="text/event-stream")


def _history_from_qa_rows(rows: list[dict]) -> list[QAMessage]:
    """qa_messages 조회 결과(한 행=질문+답변)를 _build_qa_messages()가 받는
    user/assistant 번갈아 나오는 형태로 펼친다."""
    result: list[QAMessage] = []
    for r in rows:
        result.append(QAMessage(role="user", text=r["question"]))
        result.append(QAMessage(role="assistant", text=r["answer"]))
    return result


async def _run_qa_generation(message_id: int, system: str, messages: list[dict], provider, model, queue: "asyncio.Queue") -> None:
    """LLM 호출을 요청/응답 코루틴과 분리된 독립 태스크로 돌린다. `frontend/app.js`의
    `navigate()`/`popstate`가 화면 전환 시 SSE reader를 cancel()하면 브라우저 fetch
    연결이 끊기는데, 이 함수가 그 요청 코루틴의 자식이면 같이 취소돼 LLM 호출이 중간에
    멈춘다 — 그러면 "떠났다 돌아오면 답변이 채워져 있다"는 이번 마이그레이션의 목적 자체가
    깨진다. `asyncio.create_task()`로 독립시켜서 클라이언트 연결과 무관하게 끝까지 실행하고
    DB에 결과를 남긴다(backend/routers/rag.py의 trigger_reindex_background()와 같은 원리 —
    그 함수 주석 참고: 요청 코루틴의 취소가 실제 작업까지 취소시키면 안 된다)."""
    logger.info("QnA 생성 태스크 시작 (message_id=%s)", message_id)
    full_text = ""
    try:
        async for chunk in provider.stream(system=system, messages=messages, model=model, operation="Q&A"):
            full_text += chunk
            await queue.put({"text": chunk})
        mark_qa_done(message_id, full_text)
        logger.info("QnA 생성 태스크 완료 (message_id=%s, len=%s)", message_id, len(full_text))
    except asyncio.CancelledError:
        logger.warning("QnA 생성 태스크가 취소됨 (message_id=%s) — 디버깅용, 원래는 발생하면 안 됨", message_id)
        raise
    except Exception as e:
        logger.exception("QnA 생성 태스크 실패 (message_id=%s)", message_id)
        mark_qa_failed(message_id, str(e))
        await queue.put({"error": str(e)})
    finally:
        await queue.put(None)  # 종료 시그널 — SSE 중계 제너레이터가 이걸 보고 멈춘다


def _make_sse_from_queue(queue: "asyncio.Queue", message_id: int):
    """독립 태스크(_run_qa_generation)가 큐에 채워주는 항목을 SSE로 중계만 한다. 클라이언트가
    연결을 끊어도(화면 이동 등) 이 제너레이터만 멈추고, 큐를 채우는 태스크는 계속 실행된다."""
    async def _wrapped():
        yield f"data: {json.dumps({'message_id': message_id})}\n\n"
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(_wrapped(), media_type="text/event-stream")


def _build_qa_messages(context_part: str, history: list[QAMessage], question: str) -> list[dict]:
    """대화 히스토리를 포함한 멀티턴 메시지 배열 구성. 컨텍스트는 첫 메시지에만 포함하고,
    연속으로 같은 role이 나오면 하나의 메시지로 합쳐 역할이 user/assistant로 번갈아 나오게 한다."""
    turns = [{"role": h.role, "text": h.text} for h in history]
    turns.append({"role": "user", "text": f"## 질문\n{question}"})

    messages: list[dict] = []
    for turn in turns:
        if messages and messages[-1]["role"] == turn["role"]:
            if isinstance(messages[-1]["content"], list):
                messages[-1]["content"].append({"type": "text", "text": turn["text"]})
            else:
                messages[-1]["content"] += "\n\n" + turn["text"]
            continue
        if not messages:
            messages.append({"role": turn["role"], "content": [
                {"type": "text", "text": context_part, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": turn["text"]},
            ]})
        else:
            messages.append({"role": turn["role"], "content": turn["text"]})
    return messages


@router.get("/api/companies/{slug}/qa/history")
async def qa_history(slug: str):
    """저장된 QnA 메시지 전체(pending/failed 포함) — 페이지 로드 시 이걸로 채팅 화면을
    복원한다(localStorage 대체)."""
    if not storage.read_company(slug):
        raise HTTPException(status_code=404, detail="회사를 찾을 수 없습니다.")
    return {"messages": list_qa_history(slug)}


@router.post("/api/companies/{slug}/qa")
async def company_qa(slug: str, req: QARequest):
    """단일 회사 Q&A — 회사 정보 + 후보자 프로필을 컨텍스트로 High 티어 스트리밍.
    history는 클라이언트가 안 보낸다 — qa_messages에서 서버가 직접 조회(최근 20턴,
    status='done'만)해서 조립한다(docs/chat-history-server-storage/PLAN.md 참고)."""
    record = storage.read_company(slug)
    if not record:
        raise HTTPException(status_code=404, detail="회사를 찾을 수 없습니다.")
    profile_text = storage.read_profile_text() or "후보자 프로필 없음"
    company_context = f"{record.frontmatter.model_dump_json(indent=2)}\n\n{record.body}"

    context_part = (
        f"## 후보자 프로필\n{profile_text}\n\n"
        f"## 회사 정보\n{company_context}\n\n"
        f"{_fit_history_summary(slug)}"
    )
    history = _history_from_qa_rows(list_qa_context(slug))
    messages = _build_qa_messages(context_part, history, req.question)

    message_id = insert_pending_qa(slug, req.question)
    provider, model = high_provider()
    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(
        _run_qa_generation(message_id, prompts.QA_SYSTEM, messages, provider, model, queue)
    )
    _active_qa_tasks.add(task)
    task.add_done_callback(_active_qa_tasks.discard)
    return _make_sse_from_queue(queue, message_id)


def _migrate_slug_history(slug: str, messages: list[QAMessage]) -> int:
    """user→assistant로 온전히 짝지어진 턴만 status='done'으로 삽입. 과거 버그로 응답
    없이 질문만 남았던 마지막 항목처럼 짝이 안 맞으면 건너뛴다."""
    count = 0
    i = 0
    while i < len(messages) - 1:
        if messages[i].role == "user" and messages[i + 1].role == "assistant":
            message_id = insert_pending_qa(slug, messages[i].text)
            mark_qa_done(message_id, messages[i + 1].text)
            count += 1
            i += 2
        else:
            i += 1
    return count


@router.post("/api/companies/migrate-qa")
async def migrate_qa(req: QAMigrationRequest):
    """localStorage qaHistory 전체를 1회성으로 서버 저장(qa_messages)으로 옮긴다. 기기별로
    각자 다른 이력을 갖고 있으므로 기기마다 한 번씩 호출해야 한다.
    이 슬러그에 이미 메시지가 있으면(과거 마이그레이션 성공 후 클라이언트가 응답만 못 받아
    완료 플래그를 못 세우고 재호출한 경우 등) 건너뛴다 — RAG migrate-chats의 chat_id 존재
    검사와 같은 이유(2026-08-22, 원래 "겹칠 일 없음"으로 가정했다가 이 시나리오를 놓쳤음을
    인정하고 추가)."""
    total = 0
    for slug, messages in req.history.items():
        if not storage.read_company(slug):
            continue  # 이미 삭제된 회사의 옛 이력은 옮기지 않음
        if list_qa_history(slug):
            continue  # 이미 메시지가 있는 슬러그는 재이관하지 않음(중복 방지)
        total += _migrate_slug_history(slug, messages)
    return {"inserted": total}


@router.post("/api/companies/qa")
async def multi_company_qa(req: MultiQARequest):
    """다중 회사 Q&A — 선택한 회사들의 정보를 모두 컨텍스트로 넣어 비교 질문에 답변."""
    contexts: list[str] = []
    for slug in req.slugs:
        record = storage.read_company(slug)
        if record:
            history_summary = _fit_history_summary(slug)
            contexts.append(
                f"=== {record.frontmatter.display_name} ===\n"
                f"{record.frontmatter.model_dump_json(indent=2)}\n\n{record.body}"
                + (f"\n\n{history_summary}" if history_summary else "")
            )
    if not contexts:
        raise HTTPException(status_code=404, detail="선택한 회사를 찾을 수 없습니다.")

    profile_text = storage.read_profile_text() or "후보자 프로필 없음"
    company_context = "\n\n---\n\n".join(contexts)

    context_part = f"## 후보자 프로필\n{profile_text}\n\n## 회사 정보\n{company_context}\n\n"
    provider, model = high_provider()
    gen = provider.stream(
        system=prompts.QA_SYSTEM,
        messages=_build_qa_messages(context_part, req.history, req.question),
        model=model,
        operation="Multi Q&A",
    )
    return _make_sse(gen)
