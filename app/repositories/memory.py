"""Repositorio en memoria: pruebas y desarrollo sin base de datos. NO persiste entre reinicios."""
import itertools
import threading
from datetime import datetime, timezone
from decimal import Decimal

from app.repositories.base import DUE_STATUSES, ConflictError, DuplicateError, NotFoundError, q2


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryRepository:
    def __init__(self):
        self._lock = threading.RLock()
        self._ids = {k: itertools.count(1) for k in ("user", "asset", "pred", "pf", "trade")}
        self.users, self.assets, self.preds, self.evals, self.portfolios, self.trades = {}, {}, {}, {}, {}, []

    # ------------------------------------------------------------ usuarios
    def create_user(self, username, email, password_hash):
        with self._lock:
            for u in self.users.values():
                if u["username"].lower() == username.lower() or u["email"].lower() == email.lower():
                    raise DuplicateError("Usuario o correo ya registrado.")
            uid = next(self._ids["user"])
            self.users[uid] = {"id": uid, "username": username, "email": email.lower(), "password_hash": password_hash,
                               "role": "user", "bio": None, "avatar_url": None, "is_blocked": False,
                               "created_at": _now(), "last_login_at": None}
            return dict(self.users[uid])

    def get_user(self, user_id):
        u = self.users.get(user_id)
        return dict(u) if u else None

    def find_user(self, identifier):
        ident = identifier.strip().lower()
        for u in self.users.values():
            if u["username"].lower() == ident or u["email"] == ident:
                return dict(u)
        return None

    def touch_login(self, user_id):
        with self._lock:
            if user_id in self.users:
                self.users[user_id]["last_login_at"] = _now()

    # ------------------------------------------------------------ activos
    def get_or_create_asset(self, symbol, name=None, currency=None):
        with self._lock:
            for a in self.assets.values():
                if a["symbol"] == symbol:
                    return a["id"]
            aid = next(self._ids["asset"])
            self.assets[aid] = {"id": aid, "symbol": symbol, "name": name, "currency": currency}
            return aid

    # ------------------------------------------------------------ predicciones
    def _pred_view(self, p):
        out = dict(p)
        out["symbol"] = self.assets[p["asset_id"]]["symbol"]
        out["evaluation"] = dict(self.evals[p["id"]]) if p["id"] in self.evals else None
        return out

    def add_prediction(self, data):
        with self._lock:
            pid = next(self._ids["pred"])
            row = {"id": pid, "source": data["source"], "user_id": data.get("user_id"), "asset_id": data["asset_id"],
                   "direction": data["direction"], "reference_price": float(data["reference_price"]),
                   "target_price": data.get("target_price"), "expected_return_pct": data.get("expected_return_pct"),
                   "horizon_days": data["horizon_days"], "published_at": _now(), "target_date": data["target_date"],
                   "rationale": data.get("rationale"), "indicators": data.get("indicators"),
                   "status": "pendiente", "is_public": bool(data.get("is_public", False))}
            self.preds[pid] = row
            return self._pred_view(row)

    def list_predictions(self, user_id):
        rows = [p for p in self.preds.values() if p["user_id"] == user_id]
        return [self._pred_view(p) for p in sorted(rows, key=lambda p: -p["id"])]

    def get_prediction(self, pred_id):
        p = self.preds.get(pred_id)
        return self._pred_view(p) if p else None

    def set_prediction_status(self, pred_id, status):
        with self._lock:
            if pred_id not in self.preds:
                raise NotFoundError("Predicción no encontrada.")
            self.preds[pred_id]["status"] = status

    def due_predictions(self, before_date, user_id=None):
        out = []
        for p in self.preds.values():
            if p["status"] in DUE_STATUSES and p["target_date"] < before_date and p["id"] not in self.evals \
                    and (user_id is None or p["user_id"] == user_id):
                out.append(self._pred_view(p))
        return out

    def save_evaluation(self, pred_id, evaluation, new_status):
        with self._lock:
            if pred_id not in self.preds:
                raise NotFoundError("Predicción no encontrada.")
            if pred_id in self.evals:
                raise ConflictError("La predicción ya fue evaluada.")
            self.evals[pred_id] = {"prediction_id": pred_id, "evaluated_at": _now(), **evaluation}
            self.preds[pred_id]["status"] = new_status

    # ------------------------------------------------------------ portafolios
    def create_portfolio(self, user_id, name, initial_cash, commission_pct):
        with self._lock:
            if any(p["user_id"] == user_id and p["name"].lower() == name.lower() for p in self.portfolios.values()):
                raise DuplicateError("Ya existe un portafolio con ese nombre.")
            pid = next(self._ids["pf"])
            self.portfolios[pid] = {"id": pid, "user_id": user_id, "name": name, "initial_cash": q2(initial_cash),
                                    "cash": q2(initial_cash), "commission_pct": Decimal(str(commission_pct)),
                                    "currency": None, "created_at": _now()}
            return dict(self.portfolios[pid])

    def list_portfolios(self, user_id):
        return [dict(p) for p in self.portfolios.values() if p["user_id"] == user_id]

    def get_portfolio(self, portfolio_id):
        p = self.portfolios.get(portfolio_id)
        return dict(p) if p else None

    def apply_trade(self, portfolio_id, trade, expected_cash, new_cash, currency):
        with self._lock:
            p = self.portfolios.get(portfolio_id)
            if p is None:
                raise NotFoundError("Portafolio no encontrado.")
            if q2(p["cash"]) != q2(expected_cash):
                raise ConflictError("El saldo cambió durante la operación; reintente.")
            row = {"id": next(self._ids["trade"]), "portfolio_id": portfolio_id, "symbol": trade["symbol"],
                   "side": trade["side"], "quantity": Decimal(str(trade["quantity"])), "price": Decimal(str(trade["price"])),
                   "commission": Decimal(str(trade["commission"])), "price_source": trade.get("price_source"),
                   "executed_at": _now()}
            self.trades.append(row)
            p["cash"] = q2(new_cash)
            if p["currency"] is None:
                p["currency"] = currency
            return dict(row)

    def list_trades(self, portfolio_id):
        return [dict(t) for t in self.trades if t["portfolio_id"] == portfolio_id]
