"""
Encryption and masking utilities for BYOK API keys.

Uses Fernet symmetric encryption keyed by DIFFLENS_ENCRYPTION_KEY.
Provides safe developer fallbacks while ensuring decrypted secrets are never logged or exposed in APIs.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("difflens.crypto")

# Deterministic 32-byte fallback key for local dev
_DEV_FALLBACK_RAW = b"difflens_dev_secret_key_32bytes!"
_DEV_FALLBACK_KEY = base64.urlsafe_b64encode(hashlib.sha256(_DEV_FALLBACK_RAW).digest())


def _get_fernet() -> Fernet:
    env_key = os.getenv("DIFFLENS_ENCRYPTION_KEY")
    if env_key:
        try:
            # Ensure proper urlsafe b64 format
            key_bytes = env_key.encode("utf-8")
            return Fernet(key_bytes)
        except Exception as e:
            logger.warning("Invalid DIFFLENS_ENCRYPTION_KEY, falling back to hashed key: %s", e)
            hashed_key = base64.urlsafe_b64encode(hashlib.sha256(env_key.encode("utf-8")).digest())
            return Fernet(hashed_key)

    logger.warning("DIFFLENS_ENCRYPTION_KEY is unset; using local development fallback key.")
    return Fernet(_DEV_FALLBACK_KEY)


def encrypt_secret(plain_text: str) -> str:
    """Encrypts plaintext secret into a base64 Fernet token."""
    if not plain_text:
        return ""
    f = _get_fernet()
    token_bytes = f.encrypt(plain_text.encode("utf-8"))
    return token_bytes.decode("utf-8")


def decrypt_secret(cipher_token: str) -> str:
    """Decrypts a base64 Fernet token back into plaintext."""
    if not cipher_token:
        return ""
    f = _get_fernet()
    try:
        plain_bytes = f.decrypt(cipher_token.encode("utf-8"))
        return plain_bytes.decode("utf-8")
    except InvalidToken:
        logger.error("Failed to decrypt secret: invalid token or encryption key mismatch.")
        return ""


def mask_secret(plain_text: Optional[str]) -> str:
    """
    Returns a masked version of a secret for display.
    Example: 'sk-1234567890abcdef' -> '••••cdef'
    """
    if not plain_text:
        return ""
    if len(plain_text) <= 4:
        return "••••"
    return "••••" + plain_text[-4:]
