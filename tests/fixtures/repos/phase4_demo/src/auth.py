from .tokens import create_token


def login(email: str) -> str:
    if not email:
        raise ValueError("email is required")
    return create_token(email)
