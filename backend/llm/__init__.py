"""
LLM provider 팩토리.

AnthropicProvider/OpenAIProvider는 내부에 httpx.AsyncClient 연결 풀을 포함하므로
매 요청마다 새 인스턴스를 만들면 연결 풀이 재사용되지 않는다.
싱글턴 캐시를 통해 프로세스 수명 동안 인스턴스를 재사용한다.
"""
from .base import LLMProvider
from .anthropic import AnthropicProvider
from .openai import OpenAIProvider
from config import get_active_provider

_provider_cache: dict[str, LLMProvider] = {}


def get_provider(override: str | None = None) -> LLMProvider:
    """활성 provider 또는 override로 지정한 provider 인스턴스를 반환 (캐시됨)."""
    name = override or get_active_provider()
    if name not in _provider_cache:
        if name == "claude":
            _provider_cache[name] = AnthropicProvider()
        elif name == "openai":
            _provider_cache[name] = OpenAIProvider()
        else:
            raise ValueError(f"Unknown provider: {name}")
    return _provider_cache[name]
