def display_name(email: str) -> str:
    return email.split("@", maxsplit=1)[0]
