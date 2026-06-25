from src.accounts.models import User


def display_name_for_user(user: User) -> str:
    return user.display_name


def profile_label(user: User) -> str:
    return f"Patient: {display_name_for_user(user)}"
