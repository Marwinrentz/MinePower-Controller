"""Benachrichtigungen: ntfy, Telegram, E-Mail (SMTP), Webhook.

`send()` kehrt sofort zurück und verschickt im Hintergrund: Der Regelkreis
ruft es mitten im Takt auf, und ein hängender Dienst (Zeitlimit 10–15 s)
hielte sonst die ganze Regelung an. `deliver()` verschickt und wartet – für
den Test-Knopf in den Einstellungen und für Tests.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.mime.text import MIMEText

import aiohttp
from sqlalchemy import select

from ..core.logsafe import redact
from ..core.secrets import reveal_settings
from ..db import async_session
from ..models import Setting

log = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=10)
#: Höchstens so viele Sendevorgänge gleichzeitig; weitere werden verworfen
#: (eine Störung meldet sich sonst in jedem Takt neu und staut sich auf).
_MAX_PENDING = 20


class NotificationError(RuntimeError):
    """Ein Kanal hat die Nachricht nicht angenommen."""


class Notifier:
    def __init__(self) -> None:
        self._cfg: dict = {}
        self._loaded = False
        self._tasks: set[asyncio.Task] = set()

    async def refresh_config(self) -> None:
        async with async_session() as session:
            row = await session.scalar(select(Setting).where(Setting.key == "notifications"))
        self._cfg = reveal_settings("notifications", (row.value if row else {}) or {})
        self._loaded = True

    async def send(self, message: str, title: str = "MinePower") -> None:
        """Nachricht im Hintergrund verschicken – blockiert den Aufrufer nicht."""
        if len(self._tasks) >= _MAX_PENDING:
            log.warning("Benachrichtigung verworfen (zu viele ausstehend): %s", message)
            return
        task = asyncio.create_task(self._deliver_logged(message, title), name="notify")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver_logged(self, message: str, title: str) -> None:
        try:
            for channel, error in (await self.deliver(message, title)).items():
                if error:
                    log.warning("Benachrichtigung über %s fehlgeschlagen: %s", channel, error)
        except Exception as exc:  # noqa: BLE001 – Hintergrundaufgabe darf nie laut scheitern
            log.warning("Benachrichtigung fehlgeschlagen: %s", redact(str(exc)))

    async def deliver(self, message: str, title: str = "MinePower") -> dict[str, str | None]:
        """An alle eingerichteten Kanäle senden. Ergebnis je Kanal: ``None``
        bei Erfolg, sonst die Ursache (ohne Zugangsdaten)."""
        if not self._loaded:
            await self.refresh_config()
        cfg = self._cfg
        jobs: dict[str, asyncio.Future] = {}
        if cfg.get("ntfy_url"):
            jobs["ntfy"] = self._ntfy(cfg["ntfy_url"], message, title)
        if cfg.get("telegram_token") and cfg.get("telegram_chat_id"):
            jobs["telegram"] = self._telegram(cfg["telegram_token"], cfg["telegram_chat_id"], f"{title}: {message}")
        if cfg.get("webhook_url"):
            jobs["webhook"] = self._webhook(cfg["webhook_url"], message, title)
        if cfg.get("email") and cfg.get("smtp_host"):
            jobs["email"] = asyncio.to_thread(self._email, cfg, message, title)
        results = await asyncio.gather(*jobs.values(), return_exceptions=True)
        out: dict[str, str | None] = {}
        for channel, result in zip(jobs, results):
            out[channel] = redact(f"{type(result).__name__}: {result}") if isinstance(result, BaseException) else None
        return out

    @property
    def channels(self) -> list[str]:
        cfg = self._cfg
        out = []
        if cfg.get("ntfy_url"):
            out.append("ntfy")
        if cfg.get("telegram_token") and cfg.get("telegram_chat_id"):
            out.append("telegram")
        if cfg.get("webhook_url"):
            out.append("webhook")
        if cfg.get("email") and cfg.get("smtp_host"):
            out.append("email")
        return out

    async def close(self) -> None:
        """Ausstehende Sendevorgänge beim Beenden kurz abwarten."""
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=5)

    async def _ntfy(self, url: str, message: str, title: str) -> None:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as http:
            async with http.post(url, data=message.encode(), headers={"Title": _header_safe(title)}) as resp:
                _check(resp.status, "ntfy")

    async def _telegram(self, token: str, chat_id: str, text: str) -> None:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as http:
            async with http.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            ) as resp:
                _check(resp.status, "Telegram")

    async def _webhook(self, url: str, message: str, title: str) -> None:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as http:
            async with http.post(url, json={"title": title, "message": message, "source": "minepower"}) as resp:
                _check(resp.status, "Webhook")

    def _email(self, cfg: dict, message: str, title: str) -> None:
        msg = MIMEText(message, "plain", "utf-8")
        msg["Subject"] = title
        msg["From"] = cfg.get("smtp_from", cfg["email"])
        msg["To"] = cfg["email"]
        port = int(cfg.get("smtp_port", 587))
        # Zertifikat und Hostname prüfen: Ohne Kontext nimmt smtplib jedes
        # Zertifikat an, und das SMTP-Passwort ginge an jeden, der sich
        # dazwischenschaltet. Abschaltbar für Mailserver im eigenen Netz mit
        # selbst signiertem Zertifikat (smtp_tls_verify: false).
        context = ssl.create_default_context()
        if cfg.get("smtp_tls_verify", True) is False:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        if port == 465:
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=15, context=context)
        else:
            smtp = smtplib.SMTP(cfg["smtp_host"], port, timeout=15)
        with smtp:
            if port != 465 and cfg.get("smtp_tls", True):
                smtp.starttls(context=context)
            if cfg.get("smtp_user"):
                smtp.login(cfg["smtp_user"], cfg.get("smtp_password", ""))
            smtp.send_message(msg)


def _check(status: int, channel: str) -> None:
    if status >= 400:
        raise NotificationError(f"{channel} antwortet mit HTTP {status}")


def _header_safe(value: str) -> str:
    """Kopfzeilen dürfen keine Zeilenumbrüche tragen (Header-Injection)."""
    return " ".join(str(value).split())[:200]
