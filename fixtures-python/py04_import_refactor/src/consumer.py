from src.date_utils import parse_date


def normalize_signup_date(value):
    return parse_date(value).isoformat()
