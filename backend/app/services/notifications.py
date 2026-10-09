"""Benachrichtigungen: ntfy, Telegram, E-Mail (SMTP)."""
from __future__ import annotations

import asyncio
import logging
import smtplib
from email.mime.text import MIMEText

import aiohttp
from sqlalchemy import select

from ..db import async_session
from ..models import Setting

from ..core.secrets import reveal_settings

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self) -> None:
        self._cfg: dict = {}
        self._loaded = False

    async def refresh_config(self) -> None:
        async with async_session() as session:
            row = await session.scalar(select(Setting).where(Setting.key == "notifications"))
        self._cfg = reveal_settings("notifications", (row.value if row else {}) or {})
        self._loaded = True

    async def send(self, message: str, title: str = "MinePower") -> None:
        """Best-Effort an alle konfigurierten Kanäle – Fehler nur loggen."""
        if not self._loaded:
            await self.refresh_config()
        cfg = self._cfg
        tasks = []
        if cfg.get("ntfy_url"):
            tasks.append(self._ntfy(cfg["ntfy_url"], message, title))
        if cfg.get("telegram_token") and cfg.get("telegram_chat_id"):
            tasks.append(self._telegram(cfg["telegram_token"], cfg["telegram_chat_id"], f"{title}: {message}"))
        if cfg.get("email") and cfg.get("smtp_host"):
            tasks.append(asyncio.to_thread(self._email, cfg, message, title))
        for result in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(result, Exception):
                log.warning("Benachrichtigung fehlgeschlagen: %s", result)

    async def _ntfy(self, url: str, message: str, title: str) -> None:
        async with aiohttp.ClientSession() as http:
            await http.post(url, data=message.encode(), headers={"Title": title},
                            timeout=aiohttp.ClientTimeout(total=10))

    async def _telegram(self, token: str, chat_id: str, text: str) -> None:
        async with aiohttp.ClientSession() as http:
            await http.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=aiohttp.ClientTimeout(total=10),
            )

    def _email(self, cfg: dict, message: str, title: str) -> None:
        msg = MIMEText(message)
        msg["Subject"] = title
        msg["From"] = cfg.get("smtp_from", cfg["email"])
        msg["To"] = cfg["email"]
        with smtplib.SMTP(cfg["smtp_host"], int(cfg.get("smtp_port", 587)), timeout=15) as smtp:
            if cfg.get("smtp_tls", True):
                smtp.starttls()
            if cfg.get("smtp_user"):
                smtp.login(cfg["smtp_user"], cfg.get("smtp_password", ""))
            smtp.send_message(msg)
