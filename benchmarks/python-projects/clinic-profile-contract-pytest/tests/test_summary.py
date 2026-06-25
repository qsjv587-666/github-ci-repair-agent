from src.clinic.summary import build_visit_summary


def test_build_visit_summary_contract():
    summary = build_visit_summary("p-1001")

    assert summary["title"] == "Follow-up summary for Alice Chen"
    assert summary["patient_id"] == "p-1001"
    assert summary["risk"] == "medium"
    assert "dense_breast" in summary["body"]
    assert "Alice Chen" in summary["body"]
