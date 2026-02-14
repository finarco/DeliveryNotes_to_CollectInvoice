from dataclasses import dataclass


@dataclass
class AppConfig:
    name: str
    secret_key: str
    base_currency: str
    show_prices_default: bool


@dataclass
class EmailConfig:
    enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    sender: str
    operator_cc: str


@dataclass
class SuperfakturaConfig:
    enabled: bool
    api_email: str
    api_key: str
    company_id: str
    base_url: str


@dataclass
class GopayConfig:
    enabled: bool
    goid: str
    client_id: str
    client_secret: str
    gateway_url: str
