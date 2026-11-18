"""Database models and connection."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from flask import current_app
from sqlalchemy import JSON, DateTime, Numeric, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow():
    return datetime.now(timezone.utc)


def as_utc(value):
    """SQLite returns naive datetimes; treat them as UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class Base(DeclarativeBase):
    pass


class UserSession(Base):
    """Where each WhatsApp user is in the conversation."""

    __tablename__ = "user_sessions"

    phone: Mapped[str] = mapped_column(String(32), primary_key=True)
    step: Mapped[str] = mapped_column(String(32), default="main_menu")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TransferStatus:
    AWAITING_PAYMENT = "awaiting_payment"  # waiting for the sender's naira
    PROCESSING = "processing"  # naira confirmed, starting payout
    PAYOUT_PENDING = "payout_pending"  # payout submitted, waiting for the provider's result
    COMPLETED = "completed"
    FAILED = "failed"  # payout rejected; sender needs a refund
    NEEDS_REVIEW = "needs_review"  # money received but something is off; a human must look
    CANCELLED = "cancelled"


class Transfer(Base):
    """One money transfer, from quote to payout."""

    __tablename__ = "transfers"

    reference: Mapped[str] = mapped_column(String(16), primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default=TransferStatus.AWAITING_PAYMENT)

    amount_ngn: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    fee_ngn: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    total_ngn: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    ngn_per_ghs: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    amount_ghs: Mapped[Decimal] = mapped_column(Numeric(14, 2))

    bank_code: Mapped[str] = mapped_column(String(16))
    bank_name: Mapped[str] = mapped_column(String(64))
    account_number: Mapped[str] = mapped_column(String(32))
    account_name: Mapped[str] = mapped_column(String(128))

    # Where the sender pays: bank details, or a checkout link (pay_url).
    pay_bank_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    pay_account_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    pay_account_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    pay_amount_ngn: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    pay_expires: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    pay_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # checkout link

    amount_paid_ngn: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    provider_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Database:
    def __init__(self, url):
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        self.engine = create_engine(url, **kwargs)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)


def get_db() -> Database:
    return current_app.extensions["db"]
