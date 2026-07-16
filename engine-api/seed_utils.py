import hashlib


def make_seed(*parts: str) -> int:
    raw = "-".join(str(p) for p in parts)
    return int(hashlib.sha256(raw.encode()).hexdigest(), 16) % (2**31)
