"""계정 인증 — 비밀번호 해시, 세션 토큰, 로그인 시도 제한.

원칙:
  - 비밀번호는 평문으로 저장하지 않는다. 표준 라이브러리 scrypt로 해시한다
    (외부 의존성 없이 검증된 KDF를 쓸 수 있는 방법).
  - 세션 토큰도 평문으로 저장하지 않는다. DB가 유출돼도 토큰을 그대로
    쓸 수 없도록 SHA-256 해시만 저장한다.
  - 로그인 실패는 계정 이름별로 횟수를 세서 잠시 막는다. 공개 가입 사이트라
    비밀번호를 무한정 대입해볼 수 있으면 안 된다.

한계(정직하게): 시도 제한은 프로세스 메모리에 둔다. 서버가 재시작되면
초기화되고, 인스턴스가 여러 개면 각자 따로 센다. 개인 프로젝트 규모에서는
충분하지만, 규모가 커지면 Redis 같은 공유 저장소로 옮겨야 한다.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from . import db

SESSION_TTL = 60 * 60 * 24 * 30          # 30일
SESSION_COOKIE = "ss_session"

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1

# ── 비밀번호 ─────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt,
                        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ── 세션 ─────────────────────────────────────────────────────────

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.create_session(_token_hash(token), user_id, SESSION_TTL)
    return token


def resolve_session(token: str | None) -> dict | None:
    if not token:
        return None
    return db.get_session_user(_token_hash(token))


def revoke_session(token: str | None):
    if token:
        db.delete_session(_token_hash(token))


def secure_cookies() -> bool:
    """HTTPS 배포 환경에서만 Secure 쿠키를 켠다.
    Render는 RENDER 환경변수를 자동으로 심어준다. 로컬 http 개발에서
    Secure를 켜면 쿠키가 아예 저장되지 않아 로그인이 안 되는 것처럼 보인다."""
    return bool(os.environ.get("RENDER") or os.environ.get("FORCE_SECURE_COOKIES"))


# ── 로그인 시도 제한 ─────────────────────────────────────────────

_MAX_FAILS = 8
_WINDOW = 60 * 10                          # 10분
_fails: dict[str, list[float]] = {}


def _prune(key: str, now: float):
    lst = _fails.get(key, [])
    lst = [t for t in lst if now - t < _WINDOW]
    if lst:
        _fails[key] = lst
    else:
        _fails.pop(key, None)
    return lst


def login_blocked(username: str) -> int:
    """막혀 있으면 남은 초, 아니면 0."""
    now = time.time()
    lst = _prune(username.lower(), now)
    if len(lst) >= _MAX_FAILS:
        return int(_WINDOW - (now - lst[0])) + 1
    return 0


def record_login_failure(username: str):
    _fails.setdefault(username.lower(), []).append(time.time())


def clear_login_failures(username: str):
    _fails.pop(username.lower(), None)
