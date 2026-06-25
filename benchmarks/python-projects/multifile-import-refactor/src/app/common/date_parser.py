from datetime import datetime


def parse_date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()
