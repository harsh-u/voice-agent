"""AES-256-GCM encryption for WhatsApp access tokens stored in the DB."""
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from voiceagent.config import settings


def _get_key() -> bytes:
    key_hex = settings.encryption_key
    if not key_hex or len(key_hex) < 64:
        # In dev without a key, derive a deterministic key from jwt_secret
        import hashlib
        return hashlib.sha256(settings.jwt_secret.encode()).digest()
    return bytes.fromhex(key_hex[:64])


def encrypt(plaintext: str) -> str:
    """Return base64-encoded nonce+ciphertext."""
    key = _get_key()
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt(encoded: str) -> str:
    """Decrypt a value produced by encrypt()."""
    key = _get_key()
    raw = base64.b64decode(encoded)
    nonce, ct = raw[:12], raw[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ct, None).decode()
