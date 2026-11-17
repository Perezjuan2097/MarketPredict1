"""Repositorio SQLAlchemy para PostgreSQL y SQLite."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import orm
from app.repositories.automation_sql import AutomationSQL
from app.repositories.commercial_sql import CommercialSQL
from app.repositories.dataset_sql import DatasetSQL
from app.repositories.forecast_sql import ForecastSQL
from app.repositories.organization_sql import OrganizationSQL, lock_identity_writes
from app.services.forecast_emissions import prediction_rows
from app.repositories.base import DUE_STATUSES, ConflictError, DuplicateError, NotFoundError, identity_key, q2
from app.repositories.base import AdminAccessDenied, has_admin_role, validate_admin_change


def _iso(value):
    return value.isoformat() if isinstance(value, (datetime, date)) else value


def _user(u):
    return {"id": u.id, "username": u.username, "email": u.email, "password_hash": u.password_hash, "role": u.role,
            "bio": u.bio, "avatar_url": u.avatar_url, "is_blocked": bool(u.is_blocked),
            "created_at": _iso(u.created_at), "last_login_at": _iso(u.last_login_at)}


def _f(value):
    return None if value is None else float(value)


def _pred(p, symbol, ev):
    return {"id": p.id, "source": p.source, "user_id": p.user_id, "asset_id": p.asset_id, "symbol": symbol,
            "direction": p.direction, "reference_price": _f(p.reference_price), "target_price": _f(p.target_price),
            "expected_return_pct": _f(p.expected_return_pct), "horizon_days": p.horizon_days,
            "published_at": _iso(p.published_at), "target_date": _iso(p.target_date), "rationale": p.rationale,
            "indicators": p.indicators, "status": p.status, "is_public": bool(p.is_public),
            "evaluation": None if ev is None else {
                "prediction_id": ev.prediction_id, "evaluated_at": _iso(ev.evaluated_at),
                "actual_price": _f(ev.actual_price), "actual_return_pct": _f(ev.actual_return_pct),
                "abs_error": _f(ev.abs_error), "pct_error": _f(ev.pct_error),
                "direction_correct": ev.direction_correct, "baseline_abs_error": _f(ev.baseline_abs_error)}}


def _portfolio(p):
    return {"id": p.id, "user_id": p.user_id, "name": p.name, "initial_cash": Decimal(str(p.initial_cash)),
            "cash": Decimal(str(p.cash)), "commission_pct": Decimal(str(p.commission_pct)), "currency": p.currency,
            "created_at": _iso(p.created_at)}


def _trade(t, symbol):
    return {"id": t.id, "portfolio_id": t.portfolio_id, "symbol": symbol, "side": t.side,
            "quantity": Decimal(str(t.quantity)), "price": Decimal(str(t.price)), "commission": Decimal(str(t.commission)),
            "price_source": t.price_source, "executed_at": _iso(t.executed_at)}


class SQLRepository(CommercialSQL, AutomationSQL, ForecastSQL, DatasetSQL, OrganizationSQL):
    persistent_sessions = True
    def __init__(self, engine):
        self._Session = sessionmaker(engine, expire_on_commit=False)

    # ------------------------------------------------------------ usuarios
    def create_user(self, username, email, password_hash):
        with self._Session() as s:
            u = orm.User(username=username, email=identity_key(email), password_hash=password_hash)
            s.add(u)
            try:
                s.commit()
            except IntegrityError as exc:
                s.rollback()
                raise DuplicateError("Usuario o correo ya registrado.") from exc
            s.refresh(u)
            return _user(u)

    def get_user(self, user_id):
        with self._Session() as s:
            u = s.get(orm.User, user_id)
            return _user(u) if u else None

    def find_user(self, identifier):
        ident = identity_key(identifier)
        with self._Session() as s:
            u = s.execute(select(orm.User).where(or_(orm.User.username_key == ident,
                                                     orm.User.email_key == ident))).scalar_one_or_none()
            return _user(u) if u else None

    def list_users(self, after_id, limit):
        with self._Session() as s:
            return [dict(row) for row in s.execute(
                select(orm.User.id, orm.User.username, orm.User.role, orm.User.is_blocked)
                .where(orm.User.id > after_id).order_by(orm.User.id).limit(limit)
            ).mappings()]

    def admin_update_user(self, user_id, actor_id, *, role=None, blocked=None):
        with self._Session.begin() as s:
            lock_identity_writes(s)
            actor = s.get(orm.User, actor_id) if actor_id is not None else None
            actor_data = _user(actor) if actor else None
            if actor_id is not None and not has_admin_role(actor_data):
                raise AdminAccessDenied()
            target = s.get(orm.User, user_id)
            if target is None:
                return False
            active = s.scalar(select(func.count()).select_from(orm.User).where(
                orm.User.role == "admin", orm.User.is_blocked.is_(False)))
            validate_admin_change(actor_data, _user(target), role=role, blocked=blocked, active_admins=active)
            if role is not None:
                target.role = role
            if blocked and not target.is_blocked:
                self.ensure_organization_owners_on_block(s, user_id)
            if blocked is not None:
                target.is_blocked = blocked
            if blocked:
                s.execute(delete(orm.auth_sessions).where(orm.auth_sessions.c.user_id == user_id))
            return True

    def touch_login(self, user_id):
        with self._Session() as s:
            u = s.get(orm.User, user_id)
            if u:
                u.last_login_at = func.now()
                s.commit()

    # ------------------------------------------------------------ activos
    def get_or_create_asset(self, symbol, name=None, currency=None):
        with self._Session() as s:
            a = s.execute(select(orm.Asset).where(orm.Asset.symbol == symbol)).scalars().first()
            if a:
                return a.id
            a = orm.Asset(symbol=symbol, name=name, currency=currency)
            s.add(a)
            try:
                s.commit()
            except IntegrityError:  # otro proceso lo creó a la vez
                s.rollback()
                return s.execute(select(orm.Asset).where(orm.Asset.symbol == symbol)).scalars().first().id
            return a.id

    # ------------------------------------------------------------ predicciones
    def _select_preds(self):
        return (select(orm.Prediction, orm.Asset.symbol, orm.PredictionEvaluation)
                .join(orm.Asset, orm.Asset.id == orm.Prediction.asset_id)
                .outerjoin(orm.PredictionEvaluation, orm.PredictionEvaluation.prediction_id == orm.Prediction.id))

    @staticmethod
    def _new_prediction(data):
        return orm.Prediction(
            source=data["source"], user_id=data.get("user_id"), asset_id=data["asset_id"], direction=data["direction"],
            reference_price=Decimal(str(data["reference_price"])),
            target_price=None if data.get("target_price") is None else Decimal(str(data["target_price"])),
            expected_return_pct=None if data.get("expected_return_pct") is None else Decimal(str(data["expected_return_pct"])),
            horizon_days=data["horizon_days"], target_date=date.fromisoformat(data["target_date"]),
            rationale=data.get("rationale"), indicators=data.get("indicators"), is_public=bool(data.get("is_public", False)),
            status="pendiente")

    def add_prediction(self, data):
        with self._Session.begin() as s:
            p = self._new_prediction(data)
            s.add(p)
            s.flush()
            return _pred(p, s.get(orm.Asset, p.asset_id).symbol, None)

    def save_forecast_emission(self, user_id, emission, *, expired):
        key = (user_id, emission["id"])
        symbol = emission["symbol"]
        with self._Session.begin() as s:
            insert = postgres_insert if s.bind.dialect.name == "postgresql" else sqlite_insert
            if not expired:
                # Claim first. A concurrent insert waits for commit or rollback before deciding.
                claimed = s.execute(insert(orm.ForecastEmissionSave).values(
                    user_id=user_id, emission_id=emission["id"], prediction_ids=[]
                ).on_conflict_do_nothing(index_elements=["user_id", "emission_id"])
                    .returning(orm.ForecastEmissionSave.emission_id)).scalar_one_or_none()
                if claimed is not None:
                    s.execute(insert(orm.Asset).values(symbol=symbol)
                              .on_conflict_do_nothing(index_elements=["symbol"]))
                    asset_id = s.execute(select(orm.Asset.id).where(orm.Asset.symbol == symbol)).scalar_one()
                    predictions = [self._new_prediction(row) for row in prediction_rows(user_id, asset_id, emission)]
                    s.add_all(predictions)
                    s.flush()
                    s.get(orm.ForecastEmissionSave, key).prediction_ids = [p.id for p in predictions]
                    return [_pred(p, symbol, None) for p in predictions]

            batch = s.get(orm.ForecastEmissionSave, key)
            if batch is None:
                raise ConflictError("La emisión caducó. Ejecuta los modelos nuevamente.")
            rows = s.execute(self._select_preds().where(
                orm.Prediction.user_id == user_id, orm.Prediction.id.in_(batch.prediction_ids))).all()
            by_id = {p.id: _pred(p, sym, ev) for p, sym, ev in rows}
            return [by_id[pid] for pid in batch.prediction_ids]

    def list_predictions(self, user_id):
        with self._Session() as s:
            rows = s.execute(self._select_preds().where(orm.Prediction.user_id == user_id)
                             .order_by(orm.Prediction.id.desc())).all()
            return [_pred(p, sym, ev) for p, sym, ev in rows]

    def get_prediction(self, pred_id):
        with self._Session() as s:
            row = s.execute(self._select_preds().where(orm.Prediction.id == pred_id)).first()
            return _pred(*row) if row else None

    def set_prediction_status(self, pred_id, status):
        with self._Session() as s:
            self._set_pending_status(s, pred_id, status)
            s.commit()

    @staticmethod
    def _set_pending_status(session, pred_id, status):
        result = session.execute(update(orm.Prediction).where(
            orm.Prediction.id == pred_id, orm.Prediction.status.in_(DUE_STATUSES),
        ).values(status=status))
        if not result.rowcount:
            if session.get(orm.Prediction, pred_id) is None:
                raise NotFoundError("Predicción no encontrada.")
            raise ConflictError("La predicción ya no está pendiente.")

    def due_predictions(self, before_date, user_id=None):
        q = (self._select_preds().where(orm.Prediction.target_date < date.fromisoformat(before_date),
                                        orm.Prediction.status.in_(DUE_STATUSES),
                                        orm.PredictionEvaluation.id.is_(None)))
        if user_id is not None:
            q = q.where(orm.Prediction.user_id == user_id)
        with self._Session() as s:
            return [_pred(p, sym, ev) for p, sym, ev in s.execute(q).all()]

    def save_evaluation(self, pred_id, evaluation, new_status):
        with self._Session() as s:
            self._set_pending_status(s, pred_id, new_status)
            s.add(orm.PredictionEvaluation(
                prediction_id=pred_id, actual_price=Decimal(str(evaluation["actual_price"])),
                actual_return_pct=Decimal(str(evaluation["actual_return_pct"])),
                abs_error=None if evaluation.get("abs_error") is None else Decimal(str(evaluation["abs_error"])),
                pct_error=None if evaluation.get("pct_error") is None else Decimal(str(evaluation["pct_error"])),
                direction_correct=evaluation.get("direction_correct"),
                baseline_abs_error=None if evaluation.get("baseline_abs_error") is None else Decimal(str(evaluation["baseline_abs_error"]))))
            try:
                s.commit()
            except IntegrityError as exc:
                s.rollback()
                raise ConflictError("La predicción ya fue evaluada.") from exc

    # ------------------------------------------------------------ portafolios
    def create_portfolio(self, user_id, name, initial_cash, commission_pct):
        with self._Session() as s:
            p = orm.Portfolio(user_id=user_id, name=name, initial_cash=q2(initial_cash), cash=q2(initial_cash),
                              commission_pct=Decimal(str(commission_pct)))
            s.add(p)
            try:
                s.commit()
            except IntegrityError as exc:
                s.rollback()
                raise DuplicateError("Ya existe un portafolio con ese nombre.") from exc
            s.refresh(p)
            return _portfolio(p)

    def list_portfolios(self, user_id):
        with self._Session() as s:
            rows = s.execute(select(orm.Portfolio).where(orm.Portfolio.user_id == user_id).order_by(orm.Portfolio.id)).scalars().all()
            return [_portfolio(p) for p in rows]

    def get_portfolio(self, portfolio_id):
        with self._Session() as s:
            p = s.get(orm.Portfolio, portfolio_id)
            return _portfolio(p) if p else None

    def apply_trade(self, portfolio_id, trade, expected_cash, new_cash, currency):
        asset_id = self.get_or_create_asset(trade["symbol"], currency=currency)
        with self._Session() as s:
            p = s.execute(select(orm.Portfolio).where(orm.Portfolio.id == portfolio_id).with_for_update()).scalars().first()
            if p is None:
                raise NotFoundError("Portafolio no encontrado.")
            if q2(p.cash) != q2(expected_cash):
                raise ConflictError("El saldo cambió durante la operación; reintente.")
            t = orm.Trade(portfolio_id=portfolio_id, asset_id=asset_id, side=trade["side"],
                          quantity=Decimal(str(trade["quantity"])), price=Decimal(str(trade["price"])),
                          commission=Decimal(str(trade["commission"])), price_source=trade.get("price_source"))
            s.add(t)
            p.cash = q2(new_cash)
            if p.currency is None:
                p.currency = currency
            s.commit()
            s.refresh(t)
            return _trade(t, trade["symbol"])

    def list_trades(self, portfolio_id):
        with self._Session() as s:
            rows = s.execute(select(orm.Trade, orm.Asset.symbol).join(orm.Asset, orm.Asset.id == orm.Trade.asset_id)
                             .where(orm.Trade.portfolio_id == portfolio_id).order_by(orm.Trade.id)).all()
            return [_trade(t, sym) for t, sym in rows]
