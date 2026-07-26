"""Tests for the crypto module (backend/security/crypto.py).

These tests verify the encrypt_api_key / decrypt_api_key round-trip,
randomness of the IV, and edge cases (empty strings, invalid data).
"""

import pytest

from security.crypto import encrypt_api_key, decrypt_api_key, _get_encryption_key


class TestEncryptDecryptRoundtrip:
    """Verify that encrypt → decrypt returns the original plaintext."""

    def test_roundtrip_basic(self):
        plain = "sk-my-secret-api-key-12345"
        encrypted = encrypt_api_key(plain)
        assert encrypted != plain
        assert decrypt_api_key(encrypted) == plain

    def test_roundtrip_long_key(self):
        plain = "x" * 500
        encrypted = encrypt_api_key(plain)
        assert decrypt_api_key(encrypted) == plain

    def test_roundtrip_unicode(self):
        plain = "clé_avec_accénts_é_à_ç_üñïçødé"
        encrypted = encrypt_api_key(plain)
        assert decrypt_api_key(encrypted) == plain

    def test_roundtrip_special_characters(self):
        plain = "key!@#$%^&*()_+-=[]{}|;':\",./<>?`~"
        encrypted = encrypt_api_key(plain)
        assert decrypt_api_key(encrypted) == plain


class TestEncryptRandomness:
    """Verify that encryption uses a random IV (different ciphertext each time)."""

    def test_encrypt_produces_different_output(self):
        plain = "same-plaintext-key"
        enc1 = encrypt_api_key(plain)
        enc2 = encrypt_api_key(plain)
        # With AES-256-GCM (or Fernet), a random IV means two encryptions
        # of the same plaintext produce different ciphertexts.
        assert enc1 != enc2
        # Both should still decrypt to the original
        assert decrypt_api_key(enc1) == plain
        assert decrypt_api_key(enc2) == plain


class TestEncryptEmptyString:
    """Edge case: empty string input."""

    def test_encrypt_empty_string(self):
        assert encrypt_api_key("") == ""
        assert encrypt_api_key(None) == ""  # falsy → empty

    def test_decrypt_empty_string(self):
        assert decrypt_api_key("") == ""
        assert decrypt_api_key(None) == ""


class TestDecryptInvalidData:
    """Decryption of invalid / tampered data should raise, not return plaintext."""

    def test_decrypt_invalid_token(self):
        from cryptography.fernet import InvalidToken

        with pytest.raises(InvalidToken):
            decrypt_api_key("not-a-valid-encrypted-token")

    def test_decrypt_tampered_token(self):
        from cryptography.fernet import InvalidToken

        plain = "valid-key"
        encrypted = encrypt_api_key(plain)
        # Tamper with the ciphertext
        tampered = encrypted[:-4] + "AAAA"
        with pytest.raises(InvalidToken):
            decrypt_api_key(tampered)

    def test_decrypt_wrong_key(self, monkeypatch):
        """Decryption with a different key should fail."""
        from cryptography.fernet import InvalidToken, Fernet

        # Encrypt with current key
        encrypted = encrypt_api_key("secret-key")

        # Swap in a completely different key
        new_key = Fernet.generate_key()
        monkeypatch.setenv("ENCRYPTION_KEY", new_key.decode())

        # Force re-read of the key by clearing any cache
        # The crypto module reads env on each call, so this should work
        with pytest.raises(InvalidToken):
            decrypt_api_key(encrypted)


class TestEncryptionKey:
    """Tests for _get_encryption_key."""

    def test_key_from_env(self):
        """The key should be loaded from ENCRYPTION_KEY env var."""
        key = _get_encryption_key()
        assert key is not None

    def test_key_is_fernet_instance(self):
        from cryptography.fernet import Fernet

        key = _get_encryption_key()
        assert isinstance(key, Fernet)