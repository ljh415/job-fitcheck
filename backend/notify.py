import asyncio

import discord
import slack
import telegram


async def send_notification(materials: dict) -> None:
    """설정된 채널(텔레그램/슬랙/디스코드)에 동시에 알림을 전송한다.

    채널별 send_notification()이 각자 미설정/전송 실패 및 채널별 서식 렌더링을
    자체적으로 처리하므로 여기서는 병렬 실행만 담당한다.
    """
    await asyncio.gather(
        telegram.send_notification(materials),
        slack.send_notification(materials),
        discord.send_notification(materials),
    )
