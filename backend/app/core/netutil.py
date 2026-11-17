"""Echte Client-Adresse hinter einem Reverse-Proxy.

Nur Proxys aus TRUSTED_PROXIES dürfen per X-Forwarded-For eine andere
Adresse melden. Ausgewertet wird die Kette von rechts: Die erste Adresse,
die selbst kein vertrauenswürdiger Proxy ist, ist der Client. Ein Angreifer
kann links beliebige Adressen voranstellen – die werden nie erreicht,
solange rechts davon ein nicht vertrauenswürdiger Eintrag steht.
"""
from __future__ import annotations

import ipaddress
from functools import lru_cache

from starlette.requests import HTTPConnection

_Net = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache
def _trusted(raw: str) -> tuple[_Net, ...]:
    out: list[_Net] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return tuple(out)


def _is_trusted(ip: str, nets: tuple[_Net, ...]) -> bool:
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    return any(addr in n for n in nets)


def client_ip(conn: HTTPConnection) -> str:
    from ..config import get_settings

    peer = conn.client.host if conn.client else "?"
    nets = _trusted(get_settings().trusted_proxies or "")
    if not nets or not _is_trusted(peer, nets):
        return peer
    chain = [p.strip() for p in conn.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    for hop in reversed(chain):
        if not _is_trusted(hop, nets):
            try:
                return str(ipaddress.ip_address(hop))
            except ValueError:
                return peer  # Unsinn im Header → lieber die Proxy-Adresse
    return chain[0] if chain else peer
