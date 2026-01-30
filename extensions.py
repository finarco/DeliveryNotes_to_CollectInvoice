"""Flask extensions — single instances shared across the application."""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
csrf = CSRFProtect()
limiter = Limiter(get_remote_address, storage_uri="memory://")
