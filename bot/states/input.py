"""FSM states for user text input scenarios."""

from aiogram.fsm.state import State, StatesGroup


class UiInputStates(StatesGroup):
    waiting_value = State()
