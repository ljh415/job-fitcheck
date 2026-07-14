import httpx
import logging
from config import settings

logger = logging.getLogger(__name__)


async def send_notification(message: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": settings.telegram_chat_id, "text": message},
            )
    except Exception as e:
        logger.warning("텔레그램 알림 전송 실패: %s", e)
