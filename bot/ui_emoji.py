"""Centralized emoji ID catalog for all bot UI.

All long numeric custom emoji IDs used across bot screens and keyboards
are defined here. No other bot file should hardcode emoji IDs.

Two namespaces:
- UI_ICONS  — semantic icons for screen text and keyboard buttons
- EXCHANGE_EMOJI_IDS — per-exchange brand icons (sourced from cmc-exchange-icons)

Icon IDs are sourced from tgiosicons unless noted otherwise.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# UI icons — used in message text (CustomEmoji) and keyboard buttons
# (icon_custom_emoji_id).  One canonical ID per semantic meaning.
# ---------------------------------------------------------------------------
UI_ICONS: dict[str, str] = {
    # ---- navigation / chrome ----
    "back":         "5960671702059848143",  # ◀ back arrow
    "left":         "5258236805890710909",  # ◀ previous page
    "right":        "5260450573768990626",  # ▶ next page
    "home":         "5257963315258204021",  # 🏠 home
    "noop":         "5346334548001058226",  # (placeholder — no-op button)

    # ---- actions ----
    "refresh":      "6030657343744644592",  # 🔃 circular refresh arrow
    "add":          "5274008024585871702",  # ➕ add / new
    "delete":       "6039522349517115015",  # 🗑 trash / delete
    "enable":       "5774022692642492953",  # ✅ enable / on
    "disable":      "5774077015388852135",  # 🔴 disable / off
    "toggle":       "5258096772776991776",  # toggle switch
    "input":        "5771851822897566479",  # 🔡 text input
    "filter":       "5260348422266822411",  # filter / funnel
    "reset":        "5260687119092817530",  # ↺ reset
    "search":       "6032850693348399258",  # 🔎 search

    # ---- status / feedback ----
    "ok":           "5774022692642492953",  # ✅ success / done
    "warn":         "6030563507299160824",  # ❗ warning
    "error":        "6030757850274336631",  # ❌ error / close
    "status":       "6028435952299413210",  # ℹ info / status
    "loading":      "5778226250149532337",  # 🎇 loading spinner
    "actions":      "6037397706505195857",  # 👁 view / actions list

    # ---- main sections ----
    "dashboard":    "5936143551854285132",  # 📊 stats overview
    "spot":         "5904462880941545555",  # 👛 spot balances
    "futures":      "5935913431801532272",  # 📉 futures / chart
    "dex":          "5769126056262898415",  # 👛 DEX wallet
    "transactions": "5902206159095339799",  # 💳 transactions / transfers
    "integrations": "5884479287171485878",  # 📦 connected integrations
    "settings":     "6032742198179532882",  # ⚙ settings gear
    "plan":         "5886285355279193209",  # 🏷 plan / tag
    "wallet":       "5769126056262898415",  # 👛 generic wallet
    "time":         "5775896410780079073",  # 🕓 history / time
    "admin":        "6035084557378654059",  # 👤 admin / user management
}

# ---------------------------------------------------------------------------
# Exchange brand icons — per-exchange custom emoji IDs.
# Source: C:/Users/udinc/Documents/Playground/cmc-exchange-icons/out/telegram_exchange_map.csv
# ---------------------------------------------------------------------------
EXCHANGE_EMOJI_IDS: dict[str, str] = {
    "binance":  "5323519406846812138",
    "bitget":   "5323639631571361044",
    "bybit":    "5323376182572390039",
    "gateio":   "5325532896105107438",
    "htx":      "5323621124557283448",
    "kucoin":   "5323614093695819090",
    "mexc":     "5325904651294379473",
    "okx":      "5323399865022061012",
    "bitmart":  "5325816552925205232",
    "poloniex": "5323752301448436167",
    "lbank":    "5323344863670868053",
    "coinex":   "5323588469920931646",
    "bingx":    "5323684836102152001",
    "xt":       "5323343940252901094",
}
