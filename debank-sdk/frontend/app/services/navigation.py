from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from aiogram.fsm.context import FSMContext


@dataclass(slots=True)
class ScreenMeta:
    screen_id: str
    source_route: list[str]
    payload: dict[str, Any]


class NavigationService:
    async def current(self, state: FSMContext) -> ScreenMeta:
        data = await state.get_data()
        return ScreenMeta(
            screen_id=data.get("current_screen_id", "home"),
            source_route=data.get("source_route", ["home"]),
            payload=data.get("screen_payload", {}),
        )

    async def set_root_message(self, state: FSMContext, message_id: int) -> None:
        await state.update_data(main_message_id=message_id)

    async def main_message_id(self, state: FSMContext) -> int | None:
        data = await state.get_data()
        return data.get("main_message_id")

    async def go_to(self, state: FSMContext, next_screen: ScreenMeta) -> None:
        data = await state.get_data()
        stack = list(data.get("nav_stack", []))
        current = await self.current(state)
        stack.append(asdict(current))
        await state.update_data(
            nav_stack=stack,
            current_screen_id=next_screen.screen_id,
            source_route=next_screen.source_route,
            screen_payload=next_screen.payload,
        )

    async def replace(self, state: FSMContext, screen: ScreenMeta) -> None:
        await state.update_data(
            current_screen_id=screen.screen_id,
            source_route=screen.source_route,
            screen_payload=screen.payload,
        )

    async def reset(self, state: FSMContext, screen: ScreenMeta) -> None:
        await state.update_data(
            nav_stack=[],
            current_screen_id=screen.screen_id,
            source_route=screen.source_route,
            screen_payload=screen.payload,
        )

    async def go_back(self, state: FSMContext) -> ScreenMeta:
        data = await state.get_data()
        stack = list(data.get("nav_stack", []))
        if not stack:
            root = ScreenMeta(screen_id="home", source_route=["home"], payload={})
            await self.replace(state, root)
            return root

        previous = ScreenMeta(**stack.pop())
        await state.update_data(nav_stack=stack)
        await self.replace(state, previous)
        return previous
