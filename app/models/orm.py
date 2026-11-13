"""Esquema relacional de MarketPredict (SQLAlchemy 2.0).

Solo tipos portables (funciona en PostgreSQL/Supabase y en SQLite para pruebas).
La fuente de verdad del esquema son estos modelos; las migraciones se generan con Alembic (ver docs/BASE_DE_DATOS.md).
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (JSON, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String,
                        Text, UniqueConstraint, func)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Audit:
    """Campos de auditoría comunes."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


def _created():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ------------------------------------------------------------------ usuarios
class User(Audit, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="user", server_default="user")
    bio: Mapped[Optional[str]] = mapped_column(String(500))
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500))
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("role IN ('user','admin')", name="ck_users_role"),
                      CheckConstraint("length(username) >= 3", name="ck_users_username_len"))


# ------------------------------------------------------------------ activos y modelos
class Asset(Audit, Base):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(200))
    asset_type: Mapped[Optional[str]] = mapped_column(String(20))
    currency: Mapped[Optional[str]] = mapped_column(String(10))
    exchange: Mapped[Optional[str]] = mapped_column(String(40))


class DataProviderStatus(Base):
    __tablename__ = "data_providers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ModelRun(Base):
    """Cada ejecución del motor de modelos (trazabilidad y versionado)."""
    __tablename__ = "model_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    model_id: Mapped[str] = mapped_column(String(40), nullable=False)
    model_version: Mapped[str] = mapped_column(String(20), nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    data_source: Mapped[Optional[str]] = mapped_column(String(40))
    data_points: Mapped[Optional[int]] = mapped_column(Integer)
    metrics: Mapped[Optional[dict]] = mapped_column(JSON)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_model_runs_asset_model", "asset_id", "model_id", "created_at"),
                      CheckConstraint("status IN ('ok','error')", name="ck_model_runs_status"))


class Prediction(Audit, Base):
    """Predicción de un modelo (source='model') o de un usuario (source='user', pública)."""
    __tablename__ = "predictions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    model_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("model_runs.id", ondelete="SET NULL"))
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    reference_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    target_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6))
    expected_return_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    indicators: Mapped[Optional[dict]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="pendiente", server_default="pendiente")
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    __table_args__ = (
        Index("ix_predictions_asset_status", "asset_id", "status"),
        Index("ix_predictions_user", "user_id", "published_at"),
        Index("ix_predictions_target_date", "target_date"),
        CheckConstraint("source IN ('model','user')", name="ck_predictions_source"),
        CheckConstraint("direction IN ('alcista','bajista','neutral')", name="ck_predictions_direction"),
        CheckConstraint("status IN ('pendiente','en_evaluacion','cumplida','no_cumplida','invalidada')", name="ck_predictions_status"),
        CheckConstraint("reference_price > 0", name="ck_predictions_ref_price"),
        CheckConstraint("horizon_days BETWEEN 1 AND 365", name="ck_predictions_horizon"),
        CheckConstraint("(source = 'user' AND user_id IS NOT NULL) OR source = 'model'", name="ck_predictions_user_required"),
    )


class PredictionEvaluation(Base):
    """Resultado real frente a lo predicho; una sola evaluación por predicción."""
    __tablename__ = "prediction_evaluations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id", ondelete="CASCADE"), unique=True, nullable=False)
    evaluated_at: Mapped[datetime] = _created()
    actual_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    actual_return_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    abs_error: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6))
    pct_error: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    direction_correct: Mapped[Optional[bool]] = mapped_column(Boolean)
    baseline_abs_error: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6))  # error de "mantener último precio"


# ------------------------------------------------------------------ comunidad
class Post(Audit, Base):
    __tablename__ = "posts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    prediction_id: Mapped[Optional[int]] = mapped_column(ForeignKey("predictions.id", ondelete="SET NULL"))
    title: Mapped[Optional[str]] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    __table_args__ = (Index("ix_posts_created", "created_at"), Index("ix_posts_asset", "asset_id", "created_at"),
                      CheckConstraint("length(body) BETWEEN 1 AND 5000", name="ck_posts_body_len"))


class Comment(Audit, Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("comments.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    __table_args__ = (Index("ix_comments_post", "post_id", "created_at"),
                      CheckConstraint("length(body) BETWEEN 1 AND 2000", name="ck_comments_body_len"))


class Vote(Base):
    """Voto alcista/bajista/neutral sobre una publicación; un solo voto por usuario y publicación."""
    __tablename__ = "votes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_votes_post_user"),
                      CheckConstraint("direction IN ('alcista','bajista','neutral')", name="ck_votes_direction"))


class Reaction(Base):
    """Reacción a una publicación o a un comentario (exactamente uno de los dos)."""
    __tablename__ = "reactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    post_id: Mapped[Optional[int]] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    comment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("comments.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="like", server_default="like")
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("user_id", "post_id", "kind", name="uq_reactions_post"),
                      UniqueConstraint("user_id", "comment_id", "kind", name="uq_reactions_comment"),
                      CheckConstraint("(post_id IS NOT NULL AND comment_id IS NULL) OR (post_id IS NULL AND comment_id IS NOT NULL)", name="ck_reactions_one_target"))


class Follow(Base):
    __tablename__ = "follows"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    follower_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    followed_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("follower_id", "followed_id", name="uq_follows_pair"),
                      CheckConstraint("follower_id <> followed_id", name="ck_follows_not_self"),
                      Index("ix_follows_followed", "followed_id"))


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    post_id: Mapped[Optional[int]] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    comment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("comments.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="abierto", server_default="abierto")
    resolved_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    __table_args__ = (CheckConstraint("status IN ('abierto','resuelto','descartado')", name="ck_reports_status"),
                      CheckConstraint("(post_id IS NOT NULL) OR (comment_id IS NOT NULL)", name="ck_reports_target"),
                      Index("ix_reports_status", "status", "created_at"))


# ------------------------------------------------------------------ notificaciones, alertas, watchlist
class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # señal técnica, alerta de riesgo, evento, modelo, comunidad...
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(120))  # evita repetir la misma condición
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_dedupe"),
                      Index("ix_notifications_user", "user_id", "is_read", "created_at"))


class Alert(Audit, Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(30), nullable=False)
    params: Mapped[Optional[dict]] = mapped_column(JSON)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="media", server_default="media")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="activa", server_default="activa")
    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("priority IN ('baja','media','alta')", name="ck_alerts_priority"),
                      CheckConstraint("status IN ('activa','disparada','pausada')", name="ck_alerts_status"),
                      Index("ix_alerts_user_status", "user_id", "status"))


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("user_id", "asset_id", name="uq_watchlist_user_asset"),)


# ------------------------------------------------------------------ simulador (dinero VIRTUAL)
class Portfolio(Audit, Base):
    __tablename__ = "portfolios"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    initial_cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    commission_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=Decimal("0"), server_default="0")
    currency: Mapped[Optional[str]] = mapped_column(String(10))  # se fija con la primera operación; sin conversión de divisas
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_portfolios_user_name"),
                      CheckConstraint("initial_cash > 0", name="ck_portfolios_initial"),
                      CheckConstraint("cash >= 0", name="ck_portfolios_cash"))


class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"), server_default="0")
    price_source: Mapped[Optional[str]] = mapped_column(String(40))
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (CheckConstraint("side IN ('buy','sell')", name="ck_trades_side"),
                      CheckConstraint("quantity > 0 AND price > 0", name="ck_trades_positive"),
                      Index("ix_trades_portfolio", "portfolio_id", "executed_at"))


# ------------------------------------------------------------------ auditoría
class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[Optional[str]] = mapped_column(String(40))
    entity_id: Mapped[Optional[str]] = mapped_column(String(40))
    detail: Mapped[Optional[dict]] = mapped_column(JSON)
    ip: Mapped[Optional[str]] = mapped_column(String(45))
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_audit_actor", "actor_id", "created_at"), Index("ix_audit_action", "action", "created_at"))
