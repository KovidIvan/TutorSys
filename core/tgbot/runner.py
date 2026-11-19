import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import TelegramObject
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from asgiref.sync import sync_to_async

from core.tgbot.auth import get_link_for_telegram
from core.tgbot.outbox import outbox_loop
from core.tgbot.handlers import common, teacher, head, dean

logger = logging.getLogger(__name__)


class PrivateChatMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        chat = getattr(event, 'chat', None)
        if chat is None and getattr(event, 'message', None):
            chat = event.message.chat
        if chat is None or chat.type != 'private':
            return None
        user = getattr(event, 'from_user', None)
        if user is not None:
            link = await sync_to_async(get_link_for_telegram, thread_sensitive=True)(user.id)
            data['telegram_link'] = link
            if link is None and hasattr(event, 'data') and not str(event.data).startswith(('c:consent_yes', 'c:consent_no')):
                await event.answer('Сначала привяжите аккаунт через сайт TutorSys.', show_alert=True)
                return None
        return await handler(event, data)


def create_dispatcher():
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.message.outer_middleware(PrivateChatMiddleware())
    dispatcher.callback_query.outer_middleware(PrivateChatMiddleware())
    dispatcher.include_router(teacher.router)
    dispatcher.include_router(head.router)
    dispatcher.include_router(dean.router)
    dispatcher.include_router(common.router)
    return dispatcher


async def _run(token):
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = create_dispatcher()
    outbox_task = asyncio.create_task(outbox_loop(bot))
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        outbox_task.cancel()
        await asyncio.gather(outbox_task, return_exceptions=True)
        await bot.session.close()


def run_bot(token):
    try:
        asyncio.run(_run(token))
    except KeyboardInterrupt:
        logger.info('Telegram bot stopped by operator.')
