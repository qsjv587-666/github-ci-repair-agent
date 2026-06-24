def load_user(user_id):
    return {"id": user_id, "name": " ada lovelace "}


def normalize_user(user):
    return {**user, "name": user["name"].strip().title()}


def transform(user):
    return f"{user['id']}:{user['name']}"


def build_user_label(user_id):
    return transform(load_user(user_id))
