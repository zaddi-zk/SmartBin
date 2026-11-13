"""SQLAlchemy 2 schema (section 6).

Portable where it matters: JSON/ARRAY columns use PostgreSQL types in
production and JSON on SQLite so the test suite needs no server. Money and
credits are ``NUMERIC``, never floats.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")
StrList = JSON().with_variant(ARRAY(Text()), "postgresql")
IntList = JSON().with_variant(ARRAY(Integer()), "postgresql")
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=False)
    lang: Mapped[str] = mapped_column(String(8), default="en")
    credits: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    tier: Mapped[str] = mapped_column(String(16), default="free")
    proxy_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sticky_gate: Mapped[str | None] = mapped_column(String(24), nullable=True)
    banned: Mapped[bool] = mapped_column(Boolean, default=False)
    referred_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    joined_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    free_proxy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Gate(Base):
    __tablename__ = "gates"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    label: Mapped[str] = mapped_column(String(64))
    lane: Mapped[str] = mapped_column(String(16))
    processor: Mapped[str] = mapped_column(String(24))
    primitive: Mapped[str] = mapped_column(String(32))
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    capture: Mapped[str] = mapped_column(String(16), default="none")
    cost_cr: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("1"))
    refund_on: Mapped[list[str]] = mapped_column(StrList, default=list)
    countries: Mapped[list[str]] = mapped_column(StrList, default=list)
    needs_postal: Mapped[bool] = mapped_column(Boolean, default=False)
    handles_3ds: Mapped[bool] = mapped_column(Boolean, default=False)
    key_country: Mapped[str] = mapped_column(String(2), default="US")
    ladder: Mapped[list[int]] = mapped_column(IntList, default=list)
    blurb: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_hint: Mapped[int] = mapped_column(Integer, default=100)


class GateStateRow(Base):
    __tablename__ = "gate_state"

    gate_id: Mapped[str] = mapped_column(String(24), ForeignKey("gates.id"), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default="HEALTHY")
    faults: Mapped[int] = mapped_column(Integer, default=0)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    backoff_s: Mapped[int] = mapped_column(Integer, default=60)
    p50_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    p95_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_err_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    samples: Mapped[int] = mapped_column(Integer, default=0)


class ApiKey(Base):
    __tablename__ = "keys"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    gate_id: Mapped[str] = mapped_column(String(24), ForeignKey("gates.id"))
    secret: Mapped[str] = mapped_column(Text)
    country: Mapped[str] = mapped_column(String(2), default="US")
    state: Mapped[str] = mapped_column(String(16), default="PENDING")
    livemode: Mapped[bool] = mapped_column(Boolean, default=False)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quota_left: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proxy_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    benched_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    benches: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Check(Base):
    __tablename__ = "checks"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    gate_id: Mapped[str] = mapped_column(String(24))
    pan_masked: Mapped[str] = mapped_column(String(24))
    pan_hash: Mapped[str] = mapped_column(String(64))
    verdict: Mapped[str] = mapped_column(String(16))
    code: Mapped[str] = mapped_column(String(96), default="")
    decline_code: Mapped[str] = mapped_column(String(96), default="")
    avs: Mapped[str] = mapped_column(String(8), default="")
    cvc: Mapped[str] = mapped_column(String(8), default="")
    three_ds: Mapped[str] = mapped_column(String(8), default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    provider_ref: Mapped[str] = mapped_column(String(96), default="")
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    debited: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0"))
    refunded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_checks_user_created", "user_id", "created_at"),
        Index("ix_checks_pan_hash", "pan_hash"),
    )


class LedgerEntry(Base):
    __tablename__ = "ledger"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    delta: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reason: Mapped[str] = mapped_column(String(64))
    check_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    idem_key: Mapped[str] = mapped_column(String(96), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("idem_key", name="uq_ledger_idem"),)


class Proxy(Base):
    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    country: Mapped[str] = mapped_column(String(2), default="")
    alive: Mapped[bool] = mapped_column(Boolean, default=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exclusive_user: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class CanaryCard(Base):
    __tablename__ = "canary_cards"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    pan_enc: Mapped[str] = mapped_column(Text)
    mm: Mapped[str] = mapped_column(String(2))
    yy: Mapped[str] = mapped_column(String(4))
    cvv_enc: Mapped[str] = mapped_column(Text)
    country: Mapped[str] = mapped_column(String(2), default="US")
    postal: Mapped[str] = mapped_column(String(16), default="")
    alive: Mapped[bool] = mapped_column(Boolean, default=True)
    fails: Mapped[int] = mapped_column(Integer, default=0)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BinRecordRow(Base):
    """Section 17.2: one row per looked-up BIN, with provenance beside it."""

    __tablename__ = "bin_record"

    bin: Mapped[str] = mapped_column(String(8), primary_key=True)
    parent6: Mapped[str] = mapped_column(String(6), default="")
    brand: Mapped[str | None] = mapped_column(String(32), nullable=True)
    type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    tier: Mapped[str | None] = mapped_column(String(24), nullable=True)
    prepaid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    commercial: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    debit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    bank_short: Mapped[str | None] = mapped_column(String(96), nullable=True)
    bank_legal: Mapped[str | None] = mapped_column(String(160), nullable=True)
    bank_phone: Mapped[str | None] = mapped_column(String(48), nullable=True)
    bank_site: Mapped[str | None] = mapped_column(String(160), nullable=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    iso2: Mapped[str | None] = mapped_column(String(2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    pan_len: Mapped[int | None] = mapped_column(Integer, nullable=True)
    range_lo: Mapped[str | None] = mapped_column(String(19), nullable=True)
    range_hi: Mapped[str | None] = mapped_column(String(19), nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    conflicts: Mapped[int] = mapped_column(Integer, default=0)
    flags: Mapped[list[str]] = mapped_column(StrList, default=list)
    sources_n: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ttl_days: Mapped[int] = mapped_column(Integer, default=7)


class BinSourceRow(Base):
    __tablename__ = "bin_source"

    bin: Mapped[str] = mapped_column(String(8), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    field: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Bin3dsRow(Base):
    """Probe channel (advisory): a funded canary of the same country.

    Passive reads live in :class:`Bin3dsPassiveRow` and are authoritative.
    """

    __tablename__ = "bin_3ds"

    bin: Mapped[str] = mapped_column(String(8), primary_key=True)
    probe_reads: Mapped[int] = mapped_column(Integer, default=0)
    probe_challenges: Mapped[int] = mapped_column(Integer, default=0)
    probe_last_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    probe_last_state: Mapped[str] = mapped_column(String(8), default="")
    source: Mapped[str] = mapped_column(Text, default="probe")


class Bin3dsPassiveRow(Base):
    """Passive channel (authoritative): written by real card dispatches."""

    __tablename__ = "bin_3ds_passive"

    bin: Mapped[str] = mapped_column(String(8), primary_key=True)
    reads: Mapped[int] = mapped_column(Integer, default=0)
    challenges: Mapped[int] = mapped_column(Integer, default=0)
    last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_state: Mapped[str] = mapped_column(String(8), default="")


class BinHitsRow(Base):
    __tablename__ = "bin_hits"

    bin: Mapped[str] = mapped_column(String(8), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    approved: Mapped[int] = mapped_column(Integer, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    users: Mapped[int] = mapped_column(Integer, default=0)


class BinFieldReportRow(Base):
    __tablename__ = "bin_field_report"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    bin: Mapped[str] = mapped_column(String(8))
    field: Mapped[str] = mapped_column(String(32))
    claimed: Mapped[str] = mapped_column(Text, default="")
    user_id: Mapped[int] = mapped_column(BigInteger)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GateSampleRow(Base):
    __tablename__ = "gate_samples"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    gate_id: Mapped[str] = mapped_column(String(24))
    ms: Mapped[int] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_gate_samples_gate_at", "gate_id", "at"),)


class AdminLog(Base):
    __tablename__ = "admin_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    actor: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(64), default="")
    before: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
