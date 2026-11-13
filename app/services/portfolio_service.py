"""Simulador de inversiones con dinero VIRTUAL. No existe conexión con ningún bróker."""
from decimal import ROUND_HALF_UP, Decimal

from app.repositories.base import q2
from app.security import InvalidInput, clean_symbol
from app.services.portfolio_logic import compute_positions

MAX_CASH = Decimal("1000000000")


def _dec(value, name):
    try:
        d = Decimal(str(value).strip())
    except Exception as exc:
        raise InvalidInput(f"{name} no es un número válido.") from exc
    if not d.is_finite():
        raise InvalidInput(f"{name} no es un número válido.")
    return d


def create(repo, user_id, name, initial_cash, commission_pct):
    name = str(name or "").strip()
    if not 1 <= len(name) <= 80:
        raise InvalidInput("El nombre del portafolio debe tener entre 1 y 80 caracteres.")
    cash = _dec(initial_cash, "El capital inicial")
    fee = _dec(commission_pct if commission_pct not in (None, "") else 0, "La comisión")
    if not Decimal("1") <= cash <= MAX_CASH:
        raise InvalidInput("El capital inicial debe estar entre 1 y 1.000.000.000.")
    if not Decimal("0") <= fee <= Decimal("10"):
        raise InvalidInput("La comisión debe estar entre 0 % y 10 %.")
    from app.repositories.base import DuplicateError
    try:
        return repo.create_portfolio(user_id, name, cash, fee)
    except DuplicateError as exc:
        raise InvalidInput(str(exc)) from exc


def owned(repo, user_id, portfolio_id):
    pf = repo.get_portfolio(portfolio_id)
    return pf if pf and pf["user_id"] == user_id else None


def execute_trade(repo, provider, pf, symbol, side, quantity):
    side = str(side or "").lower()
    if side not in ("buy", "sell"):
        raise InvalidInput("La operación debe ser compra (buy) o venta (sell).")
    symbol = clean_symbol(symbol)
    qty = _dec(quantity, "La cantidad").quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
    if not Decimal("0") < qty <= Decimal("1000000000"):
        raise InvalidInput("La cantidad debe ser mayor que cero.")
    quote = provider.latest(symbol)
    official = quote["symbol"]
    if pf["currency"] and quote["currency"] and pf["currency"] != quote["currency"]:
        raise InvalidInput(f"Este portafolio opera en {pf['currency']}; {official} cotiza en {quote['currency']}. No hay conversión de divisas.")
    price = Decimal(str(quote["price"]))
    gross = qty * price
    commission = (gross * Decimal(str(pf["commission_pct"])) / Decimal("100")).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    cash = Decimal(str(pf["cash"]))
    if side == "buy":
        total = gross + commission
        if total > cash:
            raise InvalidInput("Fondos insuficientes para esta compra.")
        new_cash = cash - total
    else:
        held = compute_positions(repo.list_trades(pf["id"])).get(official, {}).get("quantity", Decimal("0"))
        if qty > held:
            raise InvalidInput("No puede vender más de lo que posee (no se permiten ventas en corto).")
        new_cash = cash + gross - commission
    return repo.apply_trade(pf["id"], {"symbol": official, "side": side, "quantity": qty, "price": price,
                                       "commission": commission, "price_source": quote.get("source")},
                            expected_cash=cash, new_cash=q2(new_cash), currency=quote["currency"] or pf["currency"])


def snapshot(repo, provider, pf):
    """Posiciones valoradas al último precio disponible + totales."""
    trades = repo.list_trades(pf["id"])
    positions = compute_positions(trades)
    rows, market_total = [], Decimal("0")
    for sym, pos in positions.items():
        if pos["quantity"] <= 0 and pos["realized_pnl"] == 0:
            continue
        price = None
        if pos["quantity"] > 0:
            try:
                price = Decimal(str(provider.latest(sym)["price"]))
            except Exception:
                price = None
        mv = pos["quantity"] * price if price is not None else None
        if mv is not None:
            market_total += mv
        rows.append({"symbol": sym, "quantity": pos["quantity"], "avg_cost": pos["avg_cost"], "last_price": price,
                     "market_value": mv, "unrealized_pnl": None if mv is None else mv - pos["cost_basis"],
                     "realized_pnl": pos["realized_pnl"]})
    cash = Decimal(str(pf["cash"]))
    equity = cash + market_total
    for r in rows:
        r["weight_pct"] = None if r["market_value"] is None or equity <= 0 else r["market_value"] / equity * 100
    initial = Decimal(str(pf["initial_cash"]))
    return {"portfolio": pf, "positions": rows, "trades": trades, "cash": cash, "market_value": market_total,
            "equity": equity, "return_pct": (equity / initial - 1) * 100 if initial > 0 else None,
            "realized_pnl": sum((r["realized_pnl"] for r in rows), Decimal("0")),
            "note": "Dinero virtual; precios del último cierre disponible; sin conversión de divisas ni impuestos."}
