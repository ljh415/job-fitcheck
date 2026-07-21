"""회사 정보 기반 Q&A — SSE 스트리밍."""
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

import prompts
import storage
from llm.router import high_provider
from models import MultiQARequest, QAMessage, QARequest

router = APIRouter()


def _make_sse(gen):
    """AsyncIterator를 SSE(text/event-stream) StreamingResponse로 변환."""
    async def _wrapped():
        try:
            async for chunk in gen:
                yield f"data: {json.dumps({'text': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
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


@router.post("/api/companies/{slug}/qa")
async def company_qa(slug: str, req: QARequest):
    """단일 회사 Q&A — 회사 정보 + 후보자 프로필을 컨텍스트로 High 티어 스트리밍."""
    record = storage.read_company(slug)
    if not record:
        raise HTTPException(status_code=404, detail="회사를 찾을 수 없습니다.")
    profile_text = storage.read_profile_text() or "후보자 프로필 없음"
    company_context = f"{record.frontmatter.model_dump_json(indent=2)}\n\n{record.body}"

    context_part = f"## 후보자 프로필\n{profile_text}\n\n## 회사 정보\n{company_context}\n\n"
    provider, model = high_provider()
    gen = provider.stream(
        system=prompts.QA_SYSTEM,
        messages=_build_qa_messages(context_part, req.history, req.question),
        model=model,
        operation="Q&A",
    )
    return _make_sse(gen)


@router.post("/api/companies/qa")
async def multi_company_qa(req: MultiQARequest):
    """다중 회사 Q&A — 선택한 회사들의 정보를 모두 컨텍스트로 넣어 비교 질문에 답변."""
    contexts: list[str] = []
    for slug in req.slugs:
        record = storage.read_company(slug)
        if record:
            contexts.append(
                f"=== {record.frontmatter.display_name} ===\n"
                f"{record.frontmatter.model_dump_json(indent=2)}\n\n{record.body}"
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
