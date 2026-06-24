from dataclasses import dataclass


@dataclass
class User:
    id: int
    full_name: str


def serialize_user(user):
    return {
        "id": user.id,
        "name": user.full_name,
    }
