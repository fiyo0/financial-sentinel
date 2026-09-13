"""
Email Notification Channel Adapter.
Generates responsive HTML email digests and dispatches via SMTP or records to output sink.
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from config import config


class EmailChannel:
    def __init__(
        self,
        smtp_host: Optional[str] = None,
        recipient: Optional[str] = None
    ):
        self.smtp_host = smtp_host or config.email_smtp_host
        self.recipient = recipient or config.email_recipient

    def is_configured(self) -> bool:
        return bool(self.smtp_host and self.recipient)

    def send_email(self, subject: str, html_body: str, plain_body: str = "") -> bool:
        if not self.is_configured():
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = "sentinel@financial-agent.ai"
        msg["To"] = self.recipient

        if plain_body:
            msg.attach(MIMEText(plain_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        try:
            with smtplib.SMTP(self.smtp_host, 587, timeout=10.0) as server:
                server.starttls()
                server.sendmail(msg["From"], [self.recipient], msg.as_string())
            return True
        except Exception:
            return False
