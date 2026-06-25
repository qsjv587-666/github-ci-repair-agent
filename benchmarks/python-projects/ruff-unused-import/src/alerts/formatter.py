from decimal import Decimal


def format_alert(patient_id, severity):
    return f"{patient_id}:{severity.upper()}"
