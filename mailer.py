import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path
from socket import gaierror, timeout

from config_models import EmailConfig

logger = logging.getLogger(__name__)


class MailerError(Exception):
    """Exception raised for email sending errors."""

    pass


def send_document_email(
    config: EmailConfig,
    subject: str,
    recipient: str,
    cc: str,
    body: str,
    attachment_path: str,
):
) -> bool:
    """Send email with document attachment.

    Args:
        config: Email configuration.
        subject: Email subject.
        recipient: Email recipient address.
        cc: CC address (optional).
        body: Email body text.
        attachment_path: Path to attachment file.

    Returns:
        True if email was sent successfully.

    Raises:
        MailerError: If email sending fails.
    """
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = recipient
    if cc:
        message["Cc"] = cc
    message.set_content(body)

    attachment = Path(attachment_path)
    if not attachment.exists():
        logger.error(f"Attachment file not found: {attachment_path}")
        raise MailerError(f"Attachment file not found: {attachment_path}")

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
    try:
        logger.info(f"Sending email to {recipient} with subject: {subject}")
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as server:
            server.starttls()
            server.login(config.smtp_user, config.smtp_password)
            server.send_message(message)
        logger.info(f"Email sent successfully to {recipient}")
        return True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP authentication failed: {e}")
        raise MailerError(f"Email authentication failed: {e}")

    except smtplib.SMTPRecipientsRefused as e:
        logger.error(f"Recipients refused: {e}")
        raise MailerError(f"Email recipients refused: {e}")

    except smtplib.SMTPException as e:
        logger.error(f"SMTP error: {e}")
        raise MailerError(f"Failed to send email: {e}")

    except (gaierror, timeout) as e:
        logger.error(f"Network error while sending email: {e}")
        raise MailerError(f"Network error: could not connect to mail server: {e}")

    except OSError as e:
        logger.error(f"OS error while sending email: {e}")
        raise MailerError(f"Failed to send email: {e}")
