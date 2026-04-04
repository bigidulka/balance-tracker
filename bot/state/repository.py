"""FSM-backed repository for UI navigation state."""

from __future__ import annotations

from aiogram.fsm.context import FSMContext

from bot.state.models import NavFrame, UiState


class UiStateRepository:
    async def load(self, state: FSMContext) -> UiState:
        raw = await state.get_data()
        return UiState.from_data(raw)

    async def save(self, state: FSMContext, ui_state: UiState) -> None:
        await state.update_data(**ui_state.to_data())

    async def ensure_anchor(self, state: FSMContext, message_id: int) -> UiState:
        ui_state = await self.load(state)
        if ui_state.anchor_message_id is None:
            ui_state.anchor_message_id = message_id
            await self.save(state, ui_state)
        return ui_state

    async def set_current(
        self,
        state: FSMContext,
        *,
        route: str,
        payload: dict,
        source_route: str | None,
        push_current: bool,
    ) -> UiState:
        ui_state = await self.load(state)
        if push_current:
            ui_state.nav_stack.append(
                NavFrame(
                    route=ui_state.current_route,
                    payload=dict(ui_state.payload or {}),
                    source_route=ui_state.source_route,
                )
            )

        ui_state.current_route = route
        ui_state.payload = dict(payload or {})
        ui_state.source_route = source_route
        ui_state.revision += 1
        await self.save(state, ui_state)
        return ui_state

    async def go_back(self, state: FSMContext) -> UiState:
        ui_state = await self.load(state)
        if ui_state.nav_stack:
            prev = ui_state.nav_stack.pop()
            ui_state.current_route = prev.route
            ui_state.payload = dict(prev.payload or {})
            ui_state.source_route = prev.source_route
        else:
            ui_state.current_route = "main"
            ui_state.payload = {}
            ui_state.source_route = None
        ui_state.revision += 1
        await self.save(state, ui_state)
        return ui_state

    async def set_waiting_input(self, state: FSMContext, waiting: dict | None) -> UiState:
        ui_state = await self.load(state)
        ui_state.waiting_input = dict(waiting) if isinstance(waiting, dict) else None
        await self.save(state, ui_state)
        return ui_state

    async def recover_to_main(self, state: FSMContext) -> UiState:
        ui_state = await self.load(state)
        ui_state.current_route = "main"
        ui_state.payload = {}
        ui_state.source_route = None
        ui_state.waiting_input = None
        ui_state.nav_stack = []
        ui_state.revision += 1
        await self.save(state, ui_state)
        return ui_state
