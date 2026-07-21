import hashlib


def create_token(subject: str) -> str:
    return hashlib.sha256(subject.encode()).hexdigest()
