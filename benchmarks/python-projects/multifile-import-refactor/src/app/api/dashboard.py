from src.app.utils.date import parse_date

from src.app.services.orders import normalize_order
from src.app.services.reports import build_report_window


def build_dashboard_payload(raw_order):
    order = normalize_order(raw_order)
    window = build_report_window("2026-06-01", "2026-06-30")
    return {
        "order_id": order["order_id"],
        "created_on": order["created_on"].isoformat(),
        "generated_on": parse_date("2026-06-25").isoformat(),
        "window_start": window["start"].isoformat(),
        "window_end": window["end"].isoformat(),
    }
