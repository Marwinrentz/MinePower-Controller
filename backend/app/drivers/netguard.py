"""Adressen, die nie ein Gerät sind.

Geräteadressen sind frei eingebbar, und der Server ruft sie ab (Treiber,
Verbindungstest). Das ist gewollt – die Geräte stehen im eigenen Netz, also
sind private Adressen der Normalfall und bleiben erlaubt. Gesperrt sind nur
Ziele, hinter denen nie ein Gerät steht, die aber Zugangsdaten der
Umgebung herausgeben: die Metadatendienste von Cloud-Anbietern. Sie liegen
auf festen Adressen und antworten ohne Anmeldung jedem, der vom Server aus
fragt (Server-Side Request Forgery). Dazu die Adressen ohne Ziel
(0.0.0.0, Multicast).
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

#: Metadatendienste: AWS/GCP/Azure/OpenStack/DigitalOcean, AWS ECS, AWS IPv6,
#: Alibaba, Oracle
_METADATA_IPS = frozenset(ipaddress.ip_address(a) for a in (
    "169.254.169.254", "169.254.170.2", "fd00:ec2::254", "100.100.100.200", "192.0.0.192",
))
_METADATA_NAMES = frozenset({"metadata.google.internal", "metadata", "instance-data", "metadata.azure.internal"})


def host_of(value: str) -> str:
    """Hostteil aus 'host', 'host:port' oder einer vollständigen Adresse."""
    text = str(value).strip()
    if "://" in text:
        return (urlsplit(text).hostname or "").strip()
    if text.startswith("["):  # [IPv6]:port
        return text[1:].split("]", 1)[0]
    if text.count(":") == 1:  # host:port (IPv6 ohne Klammern hat mehrere ':')
        return text.split(":", 1)[0]
    return text


def blocked_reason(value: str) -> str | None:
    """Grund, warum die Adresse nicht abgerufen wird – oder None."""
    host = host_of(value).rstrip(".").lower()
    if not host:
        return None
    if host in _METADATA_NAMES:
        return "Adresse eines Cloud-Metadatendienstes"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip in _METADATA_IPS:
        return "Adresse eines Cloud-Metadatendienstes"
    if ip.is_unspecified or ip.is_multicast:
        return "keine Geräteadresse"
    return None
