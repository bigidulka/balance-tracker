from aiogram.fsm.state import State, StatesGroup


class WalletFlow(StatesGroup):
    waiting_wallet_input = State()
    waiting_wallet_label = State()
