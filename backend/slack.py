import httpx
import logging
from config import settings
from notify_format import build_message

logger = logging.getLogger(__name__)


async def send_notification(materials: dict) -> None:
    if not settings.slack_webhook_url:
        return
    message = build_message(materials, bold=lambda s: f"*{s}*")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(settings.slack_webhook_url, json={"text": message})
    except Exception as e:
        logger.warning("슬랙 알림 전송 실패: %s", e)
