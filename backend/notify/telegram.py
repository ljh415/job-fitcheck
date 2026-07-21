import httpx
import logging
from config import settings
from .format import build_message

logger = logging.getLogger(__name__)


async def send_notification(materials: dict) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    message = build_message(materials, bold=lambda s: f"<b>{s}</b>")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": settings.telegram_chat_id, "text": message, "parse_mode": "HTML"},
            )
            resp.raise_for_status()
    except Exception as e:
        logger.warning("텔레그램 알림 전송 실패: %s", e)
