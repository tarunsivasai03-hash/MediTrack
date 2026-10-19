"""Time-based one-time passwords (RFC 6238) plus single-use recovery codes."""

import base64
import hashlib
import hmac
import io
import json
import secrets
import time

import pyotp
import qrcode

ISSUER = "MediTrack"
STEP = 30


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def qr_data_uri(uri: str) -> str:
    """PNG QR code as a data: URI, so the secret never goes to a third-party QR service."""
    buf = io.BytesIO()
    qrcode.make(uri, box_size=6, border=2).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _clean(code: str) -> str:
    return "".join(ch for ch in (code or "") if ch.isdigit())


def verify_totp(secret: str, code: str, last_step: int | None) -> int | None:
    """
    Check a 6-digit code (one step of clock drift allowed either way).
    Returns the accepted step, or None. A step at or before `last_step` is rejected,
    so a code can't be replayed.
    """
    code = _clean(code)
    if len(code) != 6:
        return None
    totp = pyotp.TOTP(secret)
    now_step = int(time.time()) // STEP
    for offset in (0, -1, 1):
        step = now_step + offset
        if hmac.compare_digest(totp.generate_otp(step), code) and (last_step is None or step > last_step):
            return step
    return None


def verify_user_totp(user, code: str) -> bool:
    """Verify against the user's secret and remember the step (caller commits)."""
    if not user.totp_secret:
        return False
    step = verify_totp(user.totp_secret, code, user.totp_last_step)
    if step is None:
        return False
    user.totp_last_step = step
    return True


# ── Recovery codes ───────────────────────────────────────────────────────

def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace("-", "").encode()).hexdigest()


def make_recovery_codes(n: int = 8) -> tuple[list[str], str]:
    """Returns (plain codes to show once, JSON of their hashes to store)."""
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]
    return codes, json.dumps([_hash(c) for c in codes])


def use_recovery_code(user, code: str) -> bool:
    """Consume a recovery code if valid (caller commits)."""
    hashes = json.loads(user.recovery_codes or "[]")
    candidate = _hash(code or "")
    for h in hashes:
        if hmac.compare_digest(h, candidate):
            hashes.remove(h)
            user.recovery_codes = json.dumps(hashes)
            return True
    return False


def recovery_codes_left(user) -> int:
    return len(json.loads(user.recovery_codes or "[]"))
