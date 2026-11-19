from aiogram.fsm.state import State, StatesGroup


class JournalForm(StatesGroup):
    load_item = State()
    entry_date = State()
    entry_time = State()
    hours = State()
    topic = State()
    confirm = State()


class LinkForm(StatesGroup):
    consent = State()


class ImportForm(StatesGroup):
    department = State()
    year = State()
    document = State()
    confirm = State()


class DistributionForm(StatesGroup):
    load_item = State()
    teacher = State()
