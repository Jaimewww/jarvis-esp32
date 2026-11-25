"""The access token clients present in ``hello`` (S3).

The token file is the one the clients already read (``~/.gentle-dot/token`` by
default). When it is missing it is created with a fresh random token and mode 0600;
an existing file is read as is. Nothing here logs or returns the token in a message.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

TOKEN_BYTES = 32


class TokenError(RuntimeError):
    """The token file cannot be read, created or is empty."""


def load_or_create_token(path: Path) -> str:
    """The token in ``path``, creating the file (mode 0600) when it does not exist."""

    path = path.expanduser()
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = _create(path)
    except OSError as error:
        raise TokenError(f"Cannot read the access token at {path}: {error.strerror or error}") from error
    if not token:
        raise TokenError(f"The access token at {path} is empty.")
    return token


def _create(path: Path) -> str:
    token = secrets.token_urlsafe(TOKEN_BYTES)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Someone else created it meanwhile: theirs wins.
        return path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise TokenError(f"Cannot create the access token at {path}: {error.strerror or error}") from error
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    return token


def token_matches(expected: str, offered: object) -> bool:
    """Whether ``offered`` is the token, compared in constant time."""

    if not isinstance(offered, str):
        return False
    return hmac.compare_digest(expected.encode("utf-8"), offered.encode("utf-8"))
