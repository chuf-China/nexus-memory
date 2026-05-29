"""nexus_crypto.py — SQLCipher encryption support for Nexus

Provides transparent database encryption using SQLCipher.
Falls back to plain SQLite when SQLCipher is unavailable.

Usage:
  from .crypto import open_encrypted_db
  conn = open_encrypted_db("nexus.db", passphrase="secret")

  # Or with key derivation from config
  conn = open_encrypted_db("nexus.db", key_env="NEXUS_DB_KEY")

Security model:
  - Key stored in env var or keyring, never in code/config files
  - PRAGMA key → PRAGMA cipher_compatibility → open
  - Backup uses encrypted snapshot (no plaintext on disk)
  - Falls back to plain SQLite if sqlcipher3 not installed
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SQLCIPHER_AVAILABLE = False
try:
    import sqlcipher3
    _SQLCIPHER_AVAILABLE = True
except ImportError:
    try:
        # Alternative package name
        import pysqlcipher3.dbapi2 as sqlcipher3
        _SQLCIPHER_AVAILABLE = True
    except ImportError:
        pass


def sqlcipher_available() -> bool:
    """Check if SQLCipher is available."""
    return _SQLCIPHER_AVAILABLE


def _derive_key(passphrase: str, salt: bytes = b"nexus_v1") -> str:
    """Derive a 256-bit key from passphrase using PBKDF2.

    Returns hex-encoded key suitable for PRAGMA key.
    """
    key = hashlib.pbkdf2_hmac(
        "sha256",
        passphrase.encode("utf-8"),
        salt,
        iterations=100000,
        dklen=32,
    )
    return key.hex()


def open_encrypted_db(
    db_path: str,
    passphrase: Optional[str] = None,
    key_env: Optional[str] = "NEXUS_DB_KEY",
    cipher_page_size: int = 4096,
    kdf_iter: int = 256000,
) -> sqlite3.Connection:
    """Open a SQLite database with SQLCipher encryption.

    Args:
        db_path: Path to database file
        passphrase: Encryption passphrase (takes precedence over key_env)
        key_env: Environment variable name for passphrase
        cipher_page_size: SQLCipher page size (4096 default)
        kdf_iter: KDF iterations (256000 for SQLCipher 4+)

    Returns:
        sqlite3.Connection (encrypted if SQLCipher available, plain otherwise)
    """
    # Resolve passphrase
    if passphrase is None and key_env:
        passphrase = os.environ.get(key_env)

    if not passphrase:
        logger.debug("No passphrase provided, opening plain SQLite")
        return sqlite3.connect(db_path)

    if not _SQLCIPHER_AVAILABLE:
        logger.warning(
            "SQLCipher not installed — opening plain SQLite. "
            "Install with: pip install sqlcipher3"
        )
        return sqlite3.connect(db_path)

    # Derive key from passphrase
    key = _derive_key(passphrase)

    # Open encrypted connection
    conn = sqlcipher3.connect(db_path)

    # Configure cipher
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    conn.execute(f"PRAGMA cipher_page_size = {cipher_page_size}")
    conn.execute(f"PRAGMA kdf_iter = {kdf_iter}")
    conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512")
    conn.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512")

    # Verify key is correct by reading schema
    try:
        conn.execute("SELECT count(*) FROM sqlite_master")
        logger.info("SQLCipher: encrypted DB opened (%s)", db_path)
    except Exception as e:
        logger.error("SQLCipher: wrong passphrase or corrupted DB: %s", e)
        conn.close()
        raise ValueError(f"Cannot open encrypted DB: {e}")

    conn.row_factory = sqlite3.Row
    return conn


def migrate_to_encrypted(
    plain_path: str,
    encrypted_path: str,
    passphrase: str,
    cipher_page_size: int = 4096,
    kdf_iter: int = 256000,
) -> bool:
    """Migrate a plain SQLite database to SQLCipher encryption.

    Uses SQLite's online backup API for atomic migration.

    Returns: True if migration succeeded
    """
    if not _SQLCIPHER_AVAILABLE:
        logger.error("SQLCipher not installed, cannot migrate to encrypted")
        return False

    if not os.path.exists(plain_path):
        logger.error("Source DB not found: %s", plain_path)
        return False

    try:
        # Open source (plain)
        src = sqlite3.connect(plain_path)

        # Open destination (encrypted)
        key = _derive_key(passphrase)
        dst = sqlcipher3.connect(encrypted_path)
        dst.execute(f"PRAGMA key = \"x'{key}'\"")
        dst.execute(f"PRAGMA cipher_page_size = {cipher_page_size}")
        dst.execute(f"PRAGMA kdf_iter = {kdf_iter}")
        dst.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512")
        dst.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512")

        # Online backup
        src.backup(dst)

        src.close()
        dst.close()

        # Verify encrypted DB
        verify = open_encrypted_db(encrypted_path, passphrase=passphrase)
        count = verify.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
        verify.close()

        logger.info("Migration complete: %s → %s (%d objects)",
                     plain_path, encrypted_path, count)
        return True

    except Exception as e:
        logger.error("Migration failed: %s", e)
        return False


def check_db_encrypted(db_path: str) -> bool:
    """Check if a database file is SQLCipher-encrypted.

    Reads the first 16 bytes — SQLCipher databases start with
    "SQLite format 3\000" like plain SQLite, but the salt is
    embedded differently. This is a heuristic check.
    """
    try:
        with open(db_path, "rb") as f:
            header = f.read(16)
        # Plain SQLite always starts with "SQLite format 3\000"
        # SQLCipher may also start with this (depending on version)
        # but the page content will be garbage
        if header[:15] == b"SQLite format 3":
            # Looks like plain SQLite — try to read a table
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("SELECT count(*) FROM sqlite_master")
                conn.close()
                return False  # Plain SQLite, readable
            except Exception:
                conn.close()
                return True  # Readable header but can't read content = encrypted
        return True  # Doesn't look like SQLite at all = encrypted or corrupt
    except Exception:
        return False
