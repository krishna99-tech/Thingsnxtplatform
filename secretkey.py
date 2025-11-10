import secrets
secret = secrets.token_urlsafe(32)   # ~43 chars, 256 bits of entropy
print(secret)
