from src.clinic.notifications import build_followup_subject
from src.clinic.profile_service import load_patient_profile
from src.clinic.serializer import serialize_patient_profile


def build_patient_packet(patient_id):
    profile = load_patient_profile(patient_id)
    return {
        "title": f"Follow-up summary for {profile['name']}",
        "patient": serialize_patient_profile(profile),
        "subject": build_followup_subject(profile),
    }
