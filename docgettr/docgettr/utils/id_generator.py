import secrets
import string


def generate_id(prefix: str, length: int = 8) -> str:
    """Generate a prefixed random ID like 'doc_a1b2c3d4'."""
    chars = string.ascii_lowercase + string.digits
    random_part = "".join(secrets.choice(chars) for _ in range(length))
    return f"{prefix}_{random_part}"
