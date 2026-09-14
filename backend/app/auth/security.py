from __future__ import annotations

import hashlib
import secrets
import unicodedata
from functools import lru_cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

from app.core.config import Settings


MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 256
# Unicode NFKD expands a single character to at most 18 characters. Since NFKC
# preserves that decomposition, this generous raw bound cannot reject an input
# whose normalized form fits the policy, but bounds normalization work.
MAX_RAW_PASSWORD_LENGTH = MAX_PASSWORD_LENGTH * 18


class PasswordPolicyError(ValueError):
    pass


def normalize_email(value: str) -> str:
    # Match InternalUser.email's existing EmailAddress storage semantics. Unicode
    # casefold/NFKC would silently alias distinct existing addresses (e.g. ß/ss).
    return value.strip().lower()


def normalize_password(value: str) -> str:
    return unicodedata.normalize("NFKC", value)


def validate_password_input(password: str) -> str:
    """Apply the same normalization and length policy to every auth entry point."""
    if not isinstance(password, str):
        raise PasswordPolicyError("Secret must be a string.")
    if len(password) > MAX_RAW_PASSWORD_LENGTH:
        raise PasswordPolicyError("Secret input is too large.")
    normalized = normalize_password(password)
    if len(normalized) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Secret must contain at least {MIN_PASSWORD_LENGTH} Unicode characters."
        )
    if len(normalized) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Secret must contain at most {MAX_PASSWORD_LENGTH} Unicode characters."
        )
    return normalized


@lru_cache(maxsize=1)
def _common_passwords() -> frozenset[str]:
    path = Path(__file__).with_name("common_passwords.txt")
    return frozenset(
        normalize_password(line.strip()).casefold()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )


def validate_new_password(
    password: str,
    *,
    email: str,
    display_name: str,
) -> str:
    normalized = validate_password_input(password)

    folded = normalized.casefold()
    if folded in _common_passwords():
        raise PasswordPolicyError("Password is present in the offline compromised-password list.")
    if len(set(folded)) == 1:
        raise PasswordPolicyError("Password is too repetitive.")

    # This is a password-policy comparison, not email identity canonicalization:
    # compare account context in the same normalized/caseless form as the secret.
    account_context = normalize_password(normalize_email(email).partition("@")[0]).casefold()
    context_tokens = {"reawote", account_context}
    context_tokens.update(
        token for token in normalize_password(display_name).casefold().split() if len(token) >= 4
    )
    if any(len(token) >= 4 and token in folded for token in context_tokens):
        raise PasswordPolicyError("Password contains easily guessed account context.")
    return normalized


class PasswordService:
    def __init__(self, settings: Settings) -> None:
        self.hasher = PasswordHasher(
            memory_cost=settings.auth_argon2_memory_kib,
            time_cost=settings.auth_argon2_time_cost,
            parallelism=settings.auth_argon2_parallelism,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        self._dummy_hash = self.hasher.hash(
            "dummy-only-never-an-account-password-8c3b2d9e"
        )

    def hash_password(self, password: str) -> str:
        return self.hasher.hash(normalize_password(password))

    def verify_password(self, password_hash: str, password: str) -> bool:
        try:
            return self.hasher.verify(password_hash, normalize_password(password))
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def dummy_verify(self, password: str) -> None:
        self.verify_password(self._dummy_hash, password)


def generate_secret_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def rate_limit_key(email: str, client_identifier: str) -> str:
    material = f"{normalize_email(email)}\0{client_identifier}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()
