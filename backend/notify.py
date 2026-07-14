import asyncio

import discord
import slack
import telegram


async def send_notification(message: str) -> None:
    """설정된 채널(텔레그램/슬랙/디스코드)에 동시에 알림을 전송한다.

    채널별 send_notification()이 각자 미설정/전송 실패를 자체적으로 처리하므로
    여기서는 병렬 실행만 담당한다.
    """
    await asyncio.gather(
        telegram.send_notification(message),
        slack.send_notification(message),
        discord.send_notification(message),
    )
