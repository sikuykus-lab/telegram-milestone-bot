from __future__ import annotations

import smtplib
from email.message import EmailMessage


class EmailSender:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        from_email: str,
        use_tls: bool = True,
        timeout_seconds: int = 20,
    ) -> None:
        self._host = host
        self._port = int(port)
        self._username = username
        self._password = password
        self._from_email = from_email
        self._use_tls = bool(use_tls)
        self._timeout_seconds = int(timeout_seconds)

    def send_text(self, to_email: str, subject: str, body: str) -> tuple[bool, str]:
        msg = EmailMessage()
        msg["From"] = self._from_email
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body or "")
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout_seconds) as smtp:
                if self._use_tls:
                    smtp.starttls()
                if self._username:
                    smtp.login(self._username, self._password)
                smtp.send_message(msg)
            return True, ""
        except Exception as exc:
            return False, str(exc)
