"""ByteMart v3 — SQLAlchemy ORM + connection.

Source of truth: docs/phase-1-data.yaml
Connection URL: from .env (POSTGRES_*) or default to local dev.

Two roles are wired:
  - bytemart_owner      — DDL + seed (used by setup_db.py)
  - bytemart_evaluator  — runtime SELECT-only (used by Phase 2 tools)

Exposed via SQLAlchemy 2.x typed Mapped[...] models.
The `app.runs` table is DEFERRED per cross-phase invariant #4.
"""
from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
def _load_dotenv() -> None:
    """Best-effort .env load at module import time."""
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        env = Path(__file__).resolve().parent.parent / ".env"
        if env.exists():
            load_dotenv(env, override=False)
    except ImportError:
        pass


_load_dotenv()


def _build_url(role: str) -> str:
    """Build a Postgres URL for a given role from env vars.

    Defaults to local dev (Docker container).
    Reads POSTGRES_OWNER_USER / POSTGRES_EVALUATOR_USER (per role).
    """
    if role == "owner":
        user = os.getenv("POSTGRES_OWNER_USER", "bytemart_owner")
        password = os.getenv("POSTGRES_OWNER_PASSWORD", "bytemart_owner_pw")
    elif role == "evaluator":
        user = os.getenv("POSTGRES_EVALUATOR_USER", "bytemart_evaluator")
        password = os.getenv("POSTGRES_EVALUATOR_PASSWORD", "bytemart_evaluator_pw")
    else:
        raise ValueError(f"Unknown role: {role!r}; use 'owner' or 'evaluator'")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "bytemart")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


def make_engine(role: str = "evaluator", echo: bool = False):
    """Construct a SQLAlchemy engine for the given role.

    role='owner'      → connect as bytemart_owner  (DDL + seed operations)
    role='evaluator'  → connect as bytemart_evaluator (runtime SELECT-only)
    """
    return create_engine(
        _build_url(role),
        echo=echo,
        pool_pre_ping=True,
        future=True,
    )


OWNER_ENGINE = make_engine(role="owner")
EVALUATOR_ENGINE = make_engine(role="evaluator")

OwnerSession = sessionmaker(bind=OWNER_ENGINE, expire_on_commit=False)
EvaluatorSession = sessionmaker(bind=EVALUATOR_ENGINE, expire_on_commit=False)


def get_session(role: str = "evaluator") -> Session:
    """Get a SQLAlchemy Session. Defaults to read-only evaluator.

    Use role='owner' for setup_db.py and any DDL/INSERT operations.
    """
    if role == "owner":
        return OwnerSession()
    elif role == "evaluator":
        return EvaluatorSession()
    raise ValueError(f"Unknown role: {role!r}; use 'owner' or 'evaluator'")


# ---------------------------------------------------------------------------
# ORM Base
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Models (mirror app.* tables from setup_db.sql)
# ---------------------------------------------------------------------------
class Customer(Base):
    """app.customers — PK = email."""
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint(
            "account_status IN ('active','inactive','suspended')",
            name="customers_account_status_check",
        ),
        {"schema": "app"},
    )

    email: Mapped[str] = mapped_column(Text, primary_key=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    account_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'")
    )
    no_of_orders: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    orders: Mapped[list["Order"]] = relationship(
        back_populates="customer", lazy="select"
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="customer", lazy="select"
    )


# ---------------------------------------------------------------------------
# Products + OrderItems (Phase 4A)
# ---------------------------------------------------------------------------
class Product(Base):
    """app.products — SKU catalog (Phase 4A)."""
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(
            "sku ~ '^[A-Z0-9-]{3,16}$'",
            name="products_sku_format_check",
        ),
        CheckConstraint(
            "category IN ('gaming_console','gaming_desktop','game_controller',"
            "'gaming_keyboard','gaming_mouse','racing_wheel',"
            "'vr_headset','monitor','audio','service_fee')",
            name="products_category_check",
        ),
        CheckConstraint(
            "unit_price > 0",
            name="products_unit_price_check",
        ),
        {"schema": "app"},
    )

    sku: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    in_stock: Mapped[bool] = mapped_column(
        __import__("sqlalchemy").Boolean, nullable=False, server_default=text("true"),
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Products.md enrichment columns (Phase 4A). Nullable: only the 15 SKUs
    # listed in data/policies/Products.md carry enrichment data.
    warranty_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tech_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tech_details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stock_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class OrderItem(Base):
    """app.order_items — normalized line items (Phase 4A)."""
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint(
            "qty > 0",
            name="order_items_qty_check",
        ),
        CheckConstraint(
            "unit_price > 0",
            name="order_items_unit_price_check",
        ),
        CheckConstraint(
            "line_total > 0",
            name="order_items_line_total_check",
        ),
        CheckConstraint(
            "line_total = qty * unit_price",
            name="order_items_line_total_equality_check",
        ),
        Index("order_items_sku_idx", "sku"),
        Index("order_items_order_idx", "order_id"),
        {"schema": "app"},
    )

    order_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.orders.order_id", ondelete="CASCADE"), primary_key=True,
    )
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(
        Text, ForeignKey("app.products.sku"), nullable=False,
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    line_total: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    order: Mapped["Order"] = relationship(back_populates="line_items", lazy="select")
    product: Mapped[Product] = relationship(Product, lazy="select")


class Order(Base):
    """app.orders — PK = order_id (8-char [A-Za-z]{2}[0-9]{6})."""
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "order_id ~ '^[A-Za-z]{2}[0-9]{6}$'",
            name="orders_order_id_format_check",
        ),
        CheckConstraint(
            "payment_method IN ('card','upi','cod')",
            name="orders_payment_method_check",
        ),
        CheckConstraint(
            "current_status IN "
            "('placed','paid','shipped','delivered','cancelled','refunded','returned')",
            name="orders_current_status_check",
        ),
        Index("orders_customer_email_idx", "customer_email"),
        Index("orders_current_status_idx", "current_status"),
        {"schema": "app"},
    )

    order_id: Mapped[str] = mapped_column(Text, primary_key=True)
    customer_email: Mapped[str] = mapped_column(
        Text, ForeignKey("app.customers.email"), nullable=False
    )
    items: Mapped[str] = mapped_column(JSONB, nullable=False)
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    payment_method: Mapped[str] = mapped_column(Text, nullable=False)
    order_date: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expected_delivery: Mapped[Optional[str]] = mapped_column(Date, nullable=True)
    current_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'placed'")
    )
    shipping_address: Mapped[str] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    customer: Mapped[Customer] = relationship(back_populates="orders", lazy="select")
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="order", lazy="select"
    )
    line_items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", lazy="select",
        cascade="all, delete-orphan", passive_deletes=True,
    )


class Payment(Base):
    """app.payments — PK = payment_id (uuid).

    Invariant (CHECK):
      (status IN ('success','refunded','partially_refunded') AND order_id IS NOT NULL)
      OR (status = 'failed' AND order_id IS NULL)
      OR (status = 'pending')
    """
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(
            "method IN ('card','upi','cod')",
            name="payments_method_check",
        ),
        CheckConstraint(
            "status IN "
            "('pending','success','failed','refunded','partially_refunded')",
            name="payments_status_check",
        ),
        CheckConstraint(
            "(status IN ('success','refunded','partially_refunded') AND order_id IS NOT NULL) "
            "OR (status = 'failed' AND order_id IS NULL) "
            "OR (status = 'pending')",
            name="payments_invariant_check",
        ),
        Index("payments_customer_status_idx", "customer_email", "status"),
        Index("payments_order_idx", "order_id"),
        {"schema": "app"},
    )

    payment_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    customer_email: Mapped[str] = mapped_column(
        Text, ForeignKey("app.customers.email"), nullable=False
    )
    order_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("app.orders.order_id"), nullable=True
    )
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    transaction_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    customer: Mapped[Customer] = relationship(
        back_populates="payments", lazy="select"
    )
    order: Mapped[Optional[Order]] = relationship(
        back_populates="payments", lazy="select"
    )


class PolicyEmbedding(Base):
    """app.policy_embeddings — DEPRECATED in Phase 4B (replaced by Parent/Child).

    Kept in the ORM only so any lingering import doesn't crash.
    Will be removed in Phase 5.
    """
    __tablename__ = "policy_embeddings"
    __table_args__ = (
        UniqueConstraint("doc_id", "version", "chunk_seq",
                         name="policy_embeddings_doc_ver_seq_uniq"),
        {"schema": "app"},
    )

    chunk_id: Mapped[str] = mapped_column(Text, primary_key=True)
    doc_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class PolicyParent(Base):
    """app.policy_parents — Phase 4B.

    One row per ~1200-token parent (the citation context). No embedding.
    Populated by scripts/ingest_policies_parent_child.py (Phase 4B).
    """
    __tablename__ = "policy_parents"
    __table_args__ = (
        Index("policy_parents_doc_idx", "doc_id"),
        {"schema": "app"},
    )

    parent_id:   Mapped[str] = mapped_column(Text, primary_key=True)
    doc_id:      Mapped[str] = mapped_column(Text, nullable=False)
    version:     Mapped[str] = mapped_column(Text, nullable=False)
    page_from:   Mapped[int] = mapped_column(Integer, nullable=False)
    page_to:     Mapped[int] = mapped_column(Integer, nullable=False)
    text:        Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at:  Mapped[str] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    children: Mapped[list["PolicyChild"]] = relationship(
        back_populates="parent", lazy="select",
        cascade="all, delete-orphan", passive_deletes=True,
    )


class PolicyChild(Base):
    """app.policy_children — Phase 4B.

    ~300-token child slices; ivfflat cosine over the embedding column.
    Read by lookup_policy(query, top_k=3) via cosine similarity.
    """
    __tablename__ = "policy_children"
    __table_args__ = (
        UniqueConstraint("parent_id", "chunk_seq",
                         name="policy_children_parent_seq_uniq"),
        Index("policy_children_parent_idx", "parent_id"),
        Index("policy_children_doc_idx", "doc_id"),
        {"schema": "app"},
    )

    chunk_id:    Mapped[str] = mapped_column(Text, primary_key=True)
    parent_id:   Mapped[str] = mapped_column(
        Text, ForeignKey("app.policy_parents.parent_id", ondelete="CASCADE"),
        nullable=False,
    )
    doc_id:      Mapped[str] = mapped_column(Text, nullable=False)
    version:     Mapped[str] = mapped_column(Text, nullable=False)
    chunk_seq:   Mapped[int] = mapped_column(Integer, nullable=False)
    text:        Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at:  Mapped[str] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    parent: Mapped[PolicyParent] = relationship(back_populates="children", lazy="select")
