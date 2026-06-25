from src.clinic.summary import build_patient_packet


def test_multifile_profile_contract():
    payload = build_patient_packet("p-3001")

    assert payload["title"] == "Follow-up summary for Alice Chen"
    assert payload["patient"]["display_name"] == "Alice Chen"
    assert payload["patient"]["risk"] == "medium"
    assert payload["subject"] == "Follow-up reminder for Alice Chen"
