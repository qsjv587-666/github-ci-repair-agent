from src.app.utils.date import parse_date


def normalize_order(raw):
    return {
        "order_id": raw["order_id"],
        "created_on": parse_date(raw["created_at"]),
    }
