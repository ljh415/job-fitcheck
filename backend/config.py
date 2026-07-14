"""
앱 설정 모듈.

환경변수 또는 backend/.env 파일에서 설정을 로드한다.
Docker 환경에서는 env_file 없이 환경변수만 사용하고,
로컬 직접 실행 시에는 backend/.env 파일을 읽는다.

런타임 provider 전환은 글로벌 변수로 관리된다.
재시작하면 default_provider로 초기화되는 것은 의도된 동작 (PoC 단계).
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


# 루트 .env 기준 절대경로 — cwd와 무관하게 항상 동일한 위치를 가리킴
_ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""

    # 기본 provider: "claude" | "openai" | "gemini"
    default_provider: str = "claude"

    # 모델 티어 기본값 (설정 뷰에서 런타임 변경 가능)
    claude_high_model: str = "claude-sonnet-4-6"
    claude_light_model: str = "claude-haiku-4-5-20251001"
    openai_high_model: str = "gpt-5"
    openai_light_model: str = "gpt-5-mini"
    openai_reasoning_effort: str = "medium"
    gemini_high_model: str = "gemini-3.5-flash"
    gemini_light_model: str = "gemini-3.1-flash-lite"

    # 로그인 비밀번호 — 비어 있으면 인증 미적용 (로컬 개발용)
    app_secret: str = ""

    # 텔레그램 알림 — 비어 있으면 미전송
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # 슬랙 알림 — Incoming Webhook URL, 비어 있으면 미전송
    slack_webhook_url: str = ""

    # 디스코드 알림 — Incoming Webhook URL, 비어 있으면 미전송
    discord_webhook_url: str = ""

    # 데이터 루트 디렉토리 (Docker에서는 볼륨 마운트 경로)
    data_dir: Path = Path(__file__).parent.parent / "data"

    @property
    def companies_dir(self) -> Path:
        return self.data_dir / "companies"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def candidate_profile_path(self) -> Path:
        return self.data_dir / "candidate_profile.md"


settings = Settings()

# 재시작 없이 provider/모델을 전환하기 위한 런타임 상태
# PUT /api/settings 로 변경되며, 서버 재시작 시 default_provider로 리셋됨
_runtime_provider: str = settings.default_provider
_runtime_models: dict = {}


def get_active_provider() -> str:
    return _runtime_provider


def set_active_provider(provider: str) -> None:
    global _runtime_provider
    if provider not in ("claude", "openai", "gemini"):
        raise ValueError(f"Unknown provider: {provider}")
    _runtime_provider = provider


def get_model_override(key: str) -> str | None:
    return _runtime_models.get(key)


def set_model_override(key: str, model: str) -> None:
    _runtime_models[key] = model


def get_reasoning_effort() -> str:
    return _runtime_models.get("openai_reasoning_effort") or settings.openai_reasoning_effort


# 분석 완료 알림 메시지에 포함할 항목 토글 (재시작 시 기본값으로 리셋)
_notify_pref_defaults: dict = {
    "notify_strengths": True,
    "notify_gaps": True,
    "notify_jobplanet_rating": False,
    "notify_employee_count": False,
    "notify_weekly_summary": False,
}
_runtime_notify_prefs: dict = {}


def get_notify_pref(key: str) -> bool:
    return _runtime_notify_prefs.get(key, _notify_pref_defaults[key])


def set_notify_pref(key: str, value: bool) -> None:
    _runtime_notify_prefs[key] = value


def ensure_dirs() -> None:
    """앱 시작 시 필요한 디렉토리를 생성한다."""
    settings.companies_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
