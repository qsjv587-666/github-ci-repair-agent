from src.accounts.models import User


def load_user(user_id: str) -> User:
    return User(user_id=user_id, display_name=None)
