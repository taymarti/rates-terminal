"""
Signed user tokens for the request API's hello (rpc.default_identify): '<user>.<expiry>.<hmac>', with an
HMAC-SHA256 over user and expiry under a secret the server and the token issuer share (ACME_API_SECRET).
No Deephaven imports, so issuers (a login service) and tests can use it anywhere.

    token = sign_token("alice", secret, ttl_s=3600)
    verify_token(token, secret)      # "alice", or None when forged, malformed or expired
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Optional


def sign_token(user: str, secret: str, ttl_s: int = 12 * 3600) -> str:
    """A token for `user`, valid for ttl_s: '<user>.<expiry>.<hmac>' (base64url HMAC-SHA256)."""
    exp = int(time.time()) + ttl_s
    mac = hmac.new(secret.encode(), f"{user}.{exp}".encode(), hashlib.sha256).digest()
    return f"{user}.{exp}.{base64.urlsafe_b64encode(mac).decode().rstrip('=')}"


def verify_token(token: str, secret: str) -> Optional[str]:
    """The user a token was signed for, or None when it is forged, malformed or expired."""
    try:
        user, exp, sig = token.rsplit(".", 2)
        mac = hmac.new(secret.encode(), f"{user}.{exp}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(base64.urlsafe_b64encode(mac).decode().rstrip("="), sig):
            return None
        return user if int(exp) >= time.time() else None
    except (ValueError, TypeError):
        return None
