from datetime import datetime


def parse_iso_date(value):
    return datetime.strptime(value, "%Y/%m/%d").date()
