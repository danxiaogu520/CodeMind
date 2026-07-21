from .tokens import create_token


def login(email: str, password: str) -> str:
    if not email or not password:
        raise ValueError("missing credentials")
    return create_token(email)
