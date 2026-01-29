import smtplib
from email.message import EmailMessage
from pathlib import Path

from config_models import EmailConfig


def send_document_email(
    config: EmailConfig,
    subject: str,
    recipient: str,
    cc: str,
    body: str,
    attachment_path: str,
):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = recipient
    if cc:
        message["Cc"] = cc
    message.set_content(body)

    attachment = Path(attachment_path)
    message.add_attachment(
        attachment.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=attachment.name,
    )

    with smtplib.SMTP(config.smtp_host, config.smtp_port) as server:
        server.starttls()
        server.login(config.smtp_user, config.smtp_password)
        server.send_message(message)
