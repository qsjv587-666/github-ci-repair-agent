from src.app.api.dashboard import build_dashboard_payload


def test_dashboard_payload_uses_new_date_parser_path():
    payload = build_dashboard_payload({"order_id": "o-1", "created_at": "2026-06-20"})

    assert payload == {
        "order_id": "o-1",
        "created_on": "2026-06-20",
        "generated_on": "2026-06-25",
        "window_start": "2026-06-01",
        "window_end": "2026-06-30",
    }
