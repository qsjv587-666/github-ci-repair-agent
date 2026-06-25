from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    user_id: str
    display_name: str | None
