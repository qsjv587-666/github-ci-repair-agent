from src.clinic.profile_service import load_patient_profile


def build_visit_summary(patient_id):
    profile = load_patient_profile(patient_id)
    risk_factors = ", ".join(profile["risk_factors"])
    return {
        "title": f"Follow-up summary for {profile['name']}",
        "patient_id": profile["patient_id"],
        "risk": profile["risk_level"],
        "body": f"{profile['name']} has {profile['risk_level']} risk with factors: {risk_factors}.",
    }
