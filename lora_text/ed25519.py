"""
Minimal Ed25519 (RFC 8032), pure Python, for identity keys.

Used to derive a public key from the identity seed (and, later, to sign
"verified public posts"). Not constant-time: fine for key derivation and
signing on your own computer, not for servers handling hostile timing.
Checked against the `cryptography` library in tests/test_handles.py.
"""

from __future__ import annotations

import hashlib

p = 2 ** 255 - 19
q = 2 ** 252 + 27742317777372353535851937790883648493


def _inv(x: int) -> int:
    return pow(x, p - 2, p)


d = -121665 * _inv(121666) % p
_SQRT_M1 = pow(2, (p - 1) // 4, p)


def _recover_x(y: int, sign: int):
    if y >= p:
        return None
    x2 = (y * y - 1) * _inv(d * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (p + 3) // 8, p)
    if (x * x - x2) % p != 0:
        x = x * _SQRT_M1 % p
    if (x * x - x2) % p != 0:
        return None
    if (x & 1) != sign:
        x = p - x
    return x


_GY = 4 * _inv(5) % p
_GX = _recover_x(_GY, 0)
G = (_GX, _GY, 1, _GX * _GY % p)


def _add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % p
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % p
    C = 2 * P[3] * Q[3] * d % p
    D = 2 * P[2] * Q[2] % p
    E, F, Gv, H = B - A, D - C, D + C, B + A
    return (E * F % p, Gv * H % p, F * Gv % p, E * H % p)


def _mul(s: int, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _equal(P, Q) -> bool:
    return ((P[0] * Q[2] - Q[0] * P[2]) % p == 0
            and (P[1] * Q[2] - Q[1] * P[2]) % p == 0)


def _compress(P) -> bytes:
    zi = _inv(P[2])
    x, y = P[0] * zi % p, P[1] * zi % p
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % p)


def _expand(seed: bytes):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def _hq(*parts: bytes) -> int:
    return int.from_bytes(hashlib.sha512(b"".join(parts)).digest(), "little") % q


def shared_point(seed: bytes, peer_public: bytes) -> bytes | None:
    """Diffie-Hellman on the Ed25519 curve with the identity keys themselves:
    a_me * A_peer == a_peer * A_me. Returns the compressed point, or None for a
    public key that is not a valid point or gives the neutral point (small order).
    The secret scalar is a multiple of 8, which clears small-subgroup components."""
    A = _decompress(peer_public)
    if A is None:
        return None
    a, _ = _expand(seed)
    S = _mul(a, A)
    if _equal(S, (0, 1, 1, 0)):
        return None
    return _compress(S)


def public_key(seed: bytes) -> bytes:
    a, _ = _expand(seed)
    return _compress(_mul(a, G))


def sign(seed: bytes, msg: bytes) -> bytes:
    a, prefix = _expand(seed)
    A = _compress(_mul(a, G))
    r = _hq(prefix, msg)
    R = _compress(_mul(r, G))
    s = (r + _hq(R, A, msg) * a) % q
    return R + s.to_bytes(32, "little")


def verify(public: bytes, msg: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    A = _decompress(public)
    R = _decompress(signature[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= q:
        return False
    h = _hq(signature[:32], public, msg)
    return _equal(_mul(s, G), _add(R, _mul(h, A)))
