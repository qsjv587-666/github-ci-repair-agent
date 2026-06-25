from src.app.utils.date import parse_date


def build_report_window(start, end):
    return {
        "start": parse_date(start),
        "end": parse_date(end),
    }
