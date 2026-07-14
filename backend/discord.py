import httpx
import logging
from config import settings
from notify_format import build_message

logger = logging.getLogger(__name__)


async def send_notification(materials: dict) -> None:
    if not settings.discord_webhook_url:
        return
    message = build_message(materials, bold=lambda s: f"**{s}**")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(settings.discord_webhook_url, json={"content": message})
    except Exception as e:
        logger.warning("디스코드 알림 전송 실패: %s", e)
