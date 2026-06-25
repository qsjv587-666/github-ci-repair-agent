from src.accounts.models import User


def reminder_subject(user: User) -> str:
    return user.display_name
