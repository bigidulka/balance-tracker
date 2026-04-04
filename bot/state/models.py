"""State models for route-aware single-message navigation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class NavFrame:
    route: str
    payload: dict[str, Any] = field(default_factory=dict)
    source_route: str | None = None


@dataclass
class UiState:
    current_route: str = "main"
    payload: dict[str, Any] = field(default_factory=dict)
    source_route: str | None = None
    nav_stack: list[NavFrame] = field(default_factory=list)
    waiting_input: dict[str, Any] | None = None
    anchor_message_id: int | None = None
    revision: int = 0

    def to_data(self) -> dict[str, Any]:
        return {
            "current_route": self.current_route,
            "payload": self.payload,
            "source_route": self.source_route,
            "nav_stack": [asdict(frame) for frame in self.nav_stack],
            "waiting_input": self.waiting_input,
            "anchor_message_id": self.anchor_message_id,
            "revision": self.revision,
        }

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "UiState":
        stack_raw = data.get("nav_stack") or []
        stack: list[NavFrame] = []
        for item in stack_raw:
            if isinstance(item, dict):
                stack.append(
                    NavFrame(
                        route=str(item.get("route") or "main"),
                        payload=dict(item.get("payload") or {}),
                        source_route=item.get("source_route"),
                    )
                )
        return cls(
            current_route=str(data.get("current_route") or "main"),
            payload=dict(data.get("payload") or {}),
            source_route=data.get("source_route"),
            nav_stack=stack,
            waiting_input=data.get("waiting_input") if isinstance(data.get("waiting_input"), dict) else None,
            anchor_message_id=data.get("anchor_message_id") if isinstance(data.get("anchor_message_id"), int) else None,
            revision=int(data.get("revision") or 0),
        )
