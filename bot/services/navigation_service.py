"""Navigation service for route-aware transitions."""

from __future__ import annotations

from aiogram.fsm.context import FSMContext

from bot.state.repository import UiStateRepository
from bot.state.models import UiState


class NavigationService:
    def __init__(self, state_repo: UiStateRepository):
        self.state_repo = state_repo

    async def ensure_anchor(self, state: FSMContext, message_id: int) -> UiState:
        return await self.state_repo.ensure_anchor(state, message_id)

    async def current(self, state: FSMContext) -> UiState:
        return await self.state_repo.load(state)

    async def forward(
        self,
        state: FSMContext,
        *,
        route: str,
        payload: dict,
        source_route: str | None,
    ) -> UiState:
        return await self.state_repo.set_current(
            state,
            route=route,
            payload=payload,
            source_route=source_route,
            push_current=True,
        )

    async def replace_current(
        self,
        state: FSMContext,
        *,
        route: str,
        payload: dict,
        source_route: str | None,
    ) -> UiState:
        return await self.state_repo.set_current(
            state,
            route=route,
            payload=payload,
            source_route=source_route,
            push_current=False,
        )

    async def back(self, state: FSMContext) -> UiState:
        return await self.state_repo.go_back(state)

    async def set_waiting_input(self, state: FSMContext, waiting: dict | None) -> UiState:
        return await self.state_repo.set_waiting_input(state, waiting)

    async def recover(self, state: FSMContext) -> UiState:
        return await self.state_repo.recover_to_main(state)
