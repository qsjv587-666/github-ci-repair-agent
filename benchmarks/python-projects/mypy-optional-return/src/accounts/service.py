from src.accounts.models import User


def load_user(user_id: str) -> User:
    return User(user_id=user_id, display_name=None)


def display_name_for(user_id: str) -> str:
    user = load_user(user_id)
    return user.display_name
