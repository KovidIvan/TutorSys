import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from core.tgbot.auth import get_link_for_telegram, get_active_user, link_account, own_teacher, unlink_telegram, user_roles
from core.tgbot.keyboards import ActionCallback, ChoiceCallback, main_menu, inline_buttons
from core.tgbot.states import LinkForm
from core.tgbot.texts import CONSENT_TEXT, LINK_HELP, display_name, escape

router = Router(name='common')
logger = logging.getLogger(__name__)


async def authorized_user(telegram_user_id):
    link = await sync_to_async(get_link_for_telegram, thread_sensitive=True)(telegram_user_id)
    if link is None:
        return None, None, set()
    user = await sync_to_async(get_active_user, thread_sensitive=True)(link.user_id)
    if user is None:
        return None, None, set()
    roles = await sync_to_async(user_roles, thread_sensitive=True)(user)
    return link, user, roles


async def _finish_link(code, tg_user, chat_id, answer, state):
    result, info = await sync_to_async(link_account, thread_sensitive=True)(
        code, tg_user.id, chat_id, tg_user.username or '',
        timezone.now() if settings.TELEGRAM_REQUIRE_CONSENT else None,
    )
    await state.clear()
    if not result:
        messages = {
            'invalid': 'Код неверный, просрочен или уже использован. Создайте новый на сайте.',
            'limit': 'Слишком много попыток. Попробуйте снова через 15 минут.',
            'already_linked': 'Этот Telegram-аккаунт уже связан с другой учётной записью. Сначала отвяжите его.',
            'user_has_link': 'Для этой учётной записи уже привязан другой Telegram. Сначала отвяжите его на сайте.',
        }
        await answer(messages.get(info, messages['invalid']))
        return
    logger.info('Telegram account linked: telegram_user_id=%s', tg_user.id)
    user = await sync_to_async(get_active_user, thread_sensitive=True)(info)
    roles = await sync_to_async(user_roles, thread_sensitive=True)(user)
    await answer('Аккаунт TutorSys привязан. Добро пожаловать!', reply_markup=main_menu(roles))


async def _begin_link(code, event, state):
    if not code or len(code) > 20:
        await event.answer(LINK_HELP)
        return
    if settings.TELEGRAM_REQUIRE_CONSENT:
        await state.set_state(LinkForm.consent)
        await state.update_data(link_code=code)
        await event.answer(CONSENT_TEXT, reply_markup=inline_buttons([
            [('Согласен', ChoiceCallback(action='consent_yes').pack()),
             ('Отмена', ChoiceCallback(action='consent_no').pack())]
        ]))
    else:
        await _finish_link(code, event.from_user, event.chat.id, event.answer, state)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    payload = (message.text or '').partition(' ')[2].strip()
    if payload.startswith('link_'):
        await _begin_link(payload[5:], message, state)
        return
    link, user, roles = await authorized_user(message.from_user.id)
    if link is None:
        await message.answer(f'Здравствуйте! {LINK_HELP}')
    else:
        await message.answer('Главное меню TutorSys:', reply_markup=main_menu(roles))


@router.message(Command('link'))
async def link_command(message: Message, state: FSMContext):
    code = (message.text or '').partition(' ')[2].strip()
    await _begin_link(code, message, state)


@router.callback_query(ChoiceCallback.filter(F.action == 'consent_yes'))
async def consent_yes(callback: CallbackQuery, callback_data: ChoiceCallback, state: FSMContext):
    data = await state.get_data()
    code = data.get('link_code')
    if await state.get_state() != LinkForm.consent.state or not code:
        await callback.answer('Данные устарели, запросите новый код на сайте.', show_alert=True)
        return
    await callback.answer()
    await _finish_link(code, callback.from_user, callback.message.chat.id, callback.message.answer, state)


@router.callback_query(ChoiceCallback.filter(F.action == 'consent_no'))
async def consent_no(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != LinkForm.consent.state:
        await callback.answer('Данные устарели.', show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text('Привязка отменена. Для начала создайте новый код на сайте.')
    await callback.answer()


@router.message(Command('help'))
async def help_command(message: Message):
    _, user, roles = await authorized_user(message.from_user.id)
    if user is None:
        await message.answer(LINK_HELP)
        return
    await message.answer('Используйте кнопки меню или команды /menu, /whoami, /cancel, /unlink, /notify on|off.')


@router.message(Command('menu'))
async def menu_command(message: Message):
    _, user, roles = await authorized_user(message.from_user.id)
    if user is None:
        await message.answer(LINK_HELP)
        return
    await message.answer('Главное меню TutorSys:', reply_markup=main_menu(roles))


@router.message(Command('whoami'))
async def whoami(message: Message):
    _, user, roles = await authorized_user(message.from_user.id)
    if user is None:
        await message.answer(LINK_HELP)
        return
    teacher = await sync_to_async(own_teacher, thread_sensitive=True)(user)
    if teacher is None:
        name = 'Пользователь' if settings.TELEGRAM_PRIVACY_MODE == 'minimal' else escape(user.get_full_name() or user.username)
        department = '—'
    else:
        name = display_name(teacher, user)
        department = escape(teacher.department.name)
    role_names = {'teacher': 'преподаватель', 'head': 'заведующий кафедрой', 'deputy_dean': 'заместитель декана'}
    role_text = ', '.join(role_names[role] for role in sorted(roles)) or 'не назначена'
    await message.answer(f'<b>{name}</b>\nРоль: {escape(role_text)}\nКафедра: {department}')


@router.message(Command('cancel'))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer('Текущий диалог отменён.')


@router.message(Command('unlink'))
async def unlink(message: Message):
    _, user, _ = await authorized_user(message.from_user.id)
    if user is None:
        await message.answer(LINK_HELP)
        return
    await message.answer('Отвязать Telegram от учётной записи TutorSys?', reply_markup=inline_buttons([
        [('Отвязать', ChoiceCallback(action='unlink_yes').pack()),
         ('Отмена', ChoiceCallback(action='unlink_no').pack())]
    ]))


@router.callback_query(ChoiceCallback.filter(F.action == 'unlink_yes'))
async def unlink_yes(callback: CallbackQuery, state: FSMContext):
    _, user, _ = await authorized_user(callback.from_user.id)
    if user is None:
        await callback.answer(LINK_HELP, show_alert=True)
        return
    await state.clear()
    removed = await sync_to_async(unlink_telegram, thread_sensitive=True)(callback.from_user.id)
    await callback.message.edit_text('Аккаунт отвязан.' if removed else 'Аккаунт уже не привязан.')
    await callback.answer()
    logger.info('Telegram account unlinked: telegram_user_id=%s', callback.from_user.id)


@router.callback_query(ChoiceCallback.filter(F.action == 'unlink_no'))
async def unlink_no(callback: CallbackQuery):
    _, user, _ = await authorized_user(callback.from_user.id)
    if user is None:
        await callback.answer(LINK_HELP, show_alert=True)
        return
    await callback.message.edit_text('Отвязка отменена.')
    await callback.answer()


def _set_notifications(telegram_user_id, enabled):
    from core.models import TelegramLink
    return TelegramLink.objects.filter(telegram_user_id=telegram_user_id).update(notifications_enabled=enabled)


@router.message(Command('notify'))
async def notify(message: Message):
    args = (message.text or '').split()
    if len(args) != 2 or args[1].lower() not in {'on', 'off'}:
        await message.answer('Укажите команду /notify on или /notify off.')
        return
    _, user, _ = await authorized_user(message.from_user.id)
    if user is None:
        await message.answer(LINK_HELP)
        return
    enabled = args[1].lower() == 'on'
    await sync_to_async(_set_notifications, thread_sensitive=True)(message.from_user.id, enabled)
    await message.answer('Уведомления включены.' if enabled else 'Уведомления отключены.')


@router.message()
async def unlinked_or_unknown(message: Message, state: FSMContext):
    if await state.get_state():
        return
    _, user, _ = await authorized_user(message.from_user.id)
    if user is None:
        await message.answer(LINK_HELP)
