"""
앱 설정 모듈.

환경변수 또는 backend/.env 파일에서 설정을 로드한다.
Docker 환경에서는 env_file 없이 환경변수만 사용하고,
로컬 직접 실행 시에는 backend/.env 파일을 읽는다.

런타임 provider/모델/알림설정/주간요약 스케줄은 글로벌 변수로 관리되며,
data/runtime_settings.json에 저장되어 재시작 후에도 유지된다.
"""
import json
import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


# 루트 .env 기준 절대경로 — cwd와 무관하게 항상 동일한 위치를 가리킴
_ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    # utf-8-sig: 일부 편집기/PowerShell(Set-Content -Encoding UTF8)이 .env 저장 시 파일 맨 앞에
    # BOM을 붙이는데, 이걸 "utf-8"로 읽으면 첫 번째 키 이름(GOOGLE_API_KEY 등)에 보이지 않는 문자가
    # 붙어 매칭이 깨진다. utf-8-sig는 BOM이 있으면 자동으로 제거하고, 없어도 그대로 정상 동작한다.
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), env_file_encoding="utf-8-sig", extra="ignore")

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""

    # 기본 provider: "claude" | "openai" | "gemini"
    # Gemini 무료 티어로 바로 체험 가능하도록 기본값으로 설정 (품질은 Claude 권장)
    default_provider: str = "gemini"

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

    # RAG(Agentic RAG) — 3050Ti 로컬 임베딩 추론 서버 SSH 접속 정보. 비어 있어도 무방
    # (LocalEmbeddingProvider 사용 시에만 필요).
    rag_local_ssh_host: str = ""
    rag_local_ssh_port: int = 10222
    rag_local_ssh_user: str = ""
    rag_local_ssh_key_path: str = ""
    rag_local_embed_port: int = 8500

    # RAG(Agentic RAG) — PostgreSQL+pgvector 접속 정보. RAG는 opt-in 기능이라 host가 비어
    # 있으면 비활성으로 간주한다(2번 "DB 없음 원칙" 항목 참고) — rag/main의 기본값
    # ("rag-postgres")과 달리 main에서는 빈 문자열이 기본값이다.
    rag_postgres_host: str = ""
    rag_postgres_port: int = 5432
    rag_postgres_db: str = "rag"
    rag_postgres_user: str = "rag"
    rag_postgres_password: str = ""

    @property
    def companies_dir(self) -> Path:
        return self.data_dir / "companies"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def candidate_profile_path(self) -> Path:
        return self.data_dir / "candidate_profile.md"

    @property
    def rag_configured_providers(self) -> list[str]:
        """이 배포에서 실제로 사용 가능한 임베딩 provider 목록. Google은 main의 필수 키를
        재사용하므로 항상 포함, Local은 rag_local_ssh_host가 설정된 경우에만 포함(GPU 인프라를
        직접 구성한 사용자만 해당). 재색인 훅·웹 트리거·CLI·provider 검증이 이 property 하나를
        공유 참조한다 — 감지 조건이 늘어나도 여기 한 곳만 고치면 된다."""
        return ["google"] + (["local"] if self.rag_local_ssh_host else [])


settings = Settings()

# 재시작 없이 provider/모델을 전환하기 위한 런타임 상태
# PUT /api/settings 로 변경되며, _save_runtime_state()로 파일에 저장돼 재시작 후에도 유지된다.
_runtime_provider: str = settings.default_provider
_runtime_models: dict = {}


def get_active_provider() -> str:
    return _runtime_provider


def set_active_provider(provider: str) -> None:
    global _runtime_provider
    if provider not in ("claude", "openai", "gemini"):
        raise ValueError(f"Unknown provider: {provider}")
    _runtime_provider = provider
    _save_runtime_state()


def get_model_override(key: str) -> str | None:
    return _runtime_models.get(key)


def set_model_override(key: str, model: str) -> None:
    _runtime_models[key] = model
    _save_runtime_state()


def get_reasoning_effort() -> str:
    return _runtime_models.get("openai_reasoning_effort") or settings.openai_reasoning_effort


# 분석 완료 알림 메시지에 포함할 항목 토글
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
    _save_runtime_state()


# 주간 요약 알림 발송 시각 (요일 0=월~6=일, 시:분) — 기본값 월요일 09:00
_weekly_summary_schedule_defaults: dict = {"weekday": 0, "hour": 9, "minute": 0}
_runtime_weekly_summary_schedule: dict = {}


def get_weekly_summary_schedule() -> dict:
    return {**_weekly_summary_schedule_defaults, **_runtime_weekly_summary_schedule}


def set_weekly_summary_schedule(weekday: int, hour: int, minute: int) -> None:
    _runtime_weekly_summary_schedule.update(weekday=weekday, hour=hour, minute=minute)
    _save_runtime_state()


# ── 런타임 상태 영속화 ────────────────────────────────────────────────────────
# data/runtime_settings.json에 저장 — git 미추적(data/ 전체가 gitignore 대상)이며
# 재시작해도 마지막으로 선택한 provider/모델/알림설정/주간요약 스케줄을 그대로 복원한다.

_RUNTIME_SETTINGS_FILE = settings.data_dir / "runtime_settings.json"


def _load_runtime_state() -> None:
    global _runtime_provider, _runtime_models, _runtime_notify_prefs, _runtime_weekly_summary_schedule
    if not _RUNTIME_SETTINGS_FILE.exists():
        return
    try:
        data = json.loads(_RUNTIME_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    _runtime_provider = data.get("provider", _runtime_provider)
    _runtime_models = data.get("models", _runtime_models)
    _runtime_notify_prefs = data.get("notify_prefs", _runtime_notify_prefs)
    _runtime_weekly_summary_schedule = data.get("weekly_summary_schedule", _runtime_weekly_summary_schedule)


def _save_runtime_state() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "provider": _runtime_provider,
        "models": _runtime_models,
        "notify_prefs": _runtime_notify_prefs,
        "weekly_summary_schedule": _runtime_weekly_summary_schedule,
    }
    tmp = _RUNTIME_SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _RUNTIME_SETTINGS_FILE)


_load_runtime_state()


def ensure_dirs() -> None:
    """앱 시작 시 필요한 디렉토리를 생성한다."""
    settings.companies_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
