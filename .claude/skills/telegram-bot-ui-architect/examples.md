# Examples: architecture templates

## A) Screen metadata template
```python
@dataclass
class ScreenMeta:
    screen_id: str
    source_route: list[str]
    payload: dict[str, Any]

@dataclass
class NavState:
    current_screen_id: str
    stack: list[ScreenMeta]
    awaiting_input: str | None
```

## B) Forward navigation template
```python
async def go_to_screen(state: FSMContext, current: ScreenMeta, next_screen: ScreenMeta, message: Message):
    data = await state.get_data()
    stack = data.get("nav_stack", [])
    stack.append(asdict(current))
    await state.update_data(
        nav_stack=stack,
        current_screen_id=next_screen.screen_id,
        source_route=next_screen.source_route,
        screen_payload=next_screen.payload,
    )
    text, kb = build_screen(next_screen)
    await message.edit_text(**text.as_kwargs(), reply_markup=kb)
```

## C) Back navigation template (route-aware)
```python
async def go_back(state: FSMContext, message: Message):
    data = await state.get_data()
    stack = data.get("nav_stack", [])
    if not stack:
        root = resolve_safe_root(data)
        text, kb = build_screen(root)
        await state.update_data(current_screen_id=root.screen_id, source_route=root.source_route)
        await message.edit_text(**text.as_kwargs(), reply_markup=kb)
        return

    prev = stack.pop()
    screen = ScreenMeta(**prev)
    await state.update_data(
        nav_stack=stack,
        current_screen_id=screen.screen_id,
        source_route=screen.source_route,
        screen_payload=screen.payload,
    )
    text, kb = build_screen(screen)
    await message.edit_text(**text.as_kwargs(), reply_markup=kb)
```

## D) FSM input template (single-message UX)
```python
@router.callback_query(F.data == "profile:edit_name")
async def start_name_input(cb: CallbackQuery, state: FSMContext):
    await state.set_state(ProfileStates.waiting_name)
    await state.update_data(awaiting_input="profile.name")
    text = Text(Bold("Изменение имени"), "\n", "Введите новое имя сообщением.")
    await cb.message.edit_text(**text.as_kwargs(), reply_markup=name_input_keyboard())
    await cb.answer()

@router.message(ProfileStates.waiting_name)
async def finish_name_input(msg: Message, state: FSMContext, profile_service: ProfileService):
    name = (msg.text or "").strip()
    await profile_service.update_name(msg.from_user.id, name)
    try:
        await msg.delete()
    except Exception:
        pass

    screen = await build_profile_screen(user_id=msg.from_user.id)
    await state.set_state(None)
    await state.update_data(awaiting_input=None, current_screen_id=screen.screen_id)
    await msg.bot.edit_message_text(
        chat_id=msg.chat.id,
        message_id=screen.message_id,
        **screen.text.as_kwargs(),
        reply_markup=screen.keyboard,
    )
```

## E) Compliance check snippet
```text
[PASS] No send_message for navigation
[PASS] Back uses nav stack, not static parent
[PASS] Handlers do not query DB directly
[PASS] Formatting via aiogram.utils.formatting
[PASS] User input message deleted after processing
```
