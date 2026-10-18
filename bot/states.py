from aiogram.fsm.state import State, StatesGroup


class DateInput(StatesGroup):
    waiting = State()


class GenderInput(StatesGroup):
    waiting = State()


class BroadcastInput(StatesGroup):
    text = State()
