from dataclasses import dataclass
from typing import Literal

from telegram.ext import ContextTypes

KEY = "awaiting"


@dataclass(frozen=True)
class Awaiting:
    kind: Literal[
        "note_edit",
        "contact_rename",
        "contact_nicknames",
        "destination_add",
        "destination_value",
    ]
    target_id: int


def get_awaiting(context: ContextTypes.DEFAULT_TYPE) -> Awaiting | None:
    return context.user_data.get(KEY)


def set_awaiting(context: ContextTypes.DEFAULT_TYPE, awaiting: Awaiting) -> None:
    context.user_data[KEY] = awaiting


def clear_awaiting(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(KEY, None)
