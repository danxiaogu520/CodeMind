"""Authentication service used by CodeMind tests."""

from .tokens import create_token


class AuthService:
    def login(self, email: str, password: str) -> str:
        if not email or not password:
            raise ValueError("missing credentials")
        return create_token(email)
