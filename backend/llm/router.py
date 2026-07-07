"""
작업 유형별 provider + 모델 티어 선택.

Lightweight 티어: 구조화 추출, 마크다운 본문 생성
    — 입력/출력 포맷이 고정적이므로 저렴한 모델로 충분

High 티어: PDF 프로필 추출, 적합도 평가, Q&A
    — 후보자 맥락 통합 추론이 필요하므로 고성능 모델 사용
"""
from config import get_active_provider, get_model_override, settings
from .base import LLMProvider
from . import get_provider


def _model(tier: str, provider: str) -> str:
    """런타임 오버라이드가 있으면 그것을, 없으면 설정 기본값을 반환."""
    key = f"{provider}_{tier}_model"
    override = get_model_override(key)
    if override:
        return override
    if provider == "claude":
        return settings.claude_high_model if tier == "high" else settings.claude_light_model
    if provider == "gemini":
        return settings.gemini_high_model if tier == "high" else settings.gemini_light_model
    return settings.openai_high_model if tier == "high" else settings.openai_light_model


def light_provider() -> tuple[LLMProvider, str]:
    """Lightweight 티어 (구조화 추출, 본문 생성)."""
    provider_name = get_active_provider()
    return get_provider(provider_name), _model("light", provider_name)


def high_provider() -> tuple[LLMProvider, str]:
    """High 티어 (PDF 추출, 적합도 평가, Q&A)."""
    provider_name = get_active_provider()
    return get_provider(provider_name), _model("high", provider_name)
