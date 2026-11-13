"""Cálculo de posiciones por costo promedio (puro y probado). Dinero VIRTUAL."""
from decimal import Decimal

ZERO = Decimal("0")


def compute_positions(trades):
    """trades en orden cronológico (id ascendente). Las comisiones de compra se capitalizan en el costo;
    las de venta restan de la ganancia realizada."""
    book = {}
    for t in sorted(trades, key=lambda t: t["id"]):
        sym = t["symbol"]
        qty, price, fee = Decimal(str(t["quantity"])), Decimal(str(t["price"])), Decimal(str(t["commission"]))
        pos = book.setdefault(sym, {"quantity": ZERO, "cost": ZERO, "realized_pnl": ZERO})
        if t["side"] == "buy":
            pos["quantity"] += qty
            pos["cost"] += qty * price + fee
        else:
            if qty > pos["quantity"]:
                raise ValueError(f"Historial inconsistente: venta de {sym} superior a la posición.")
            avg = pos["cost"] / pos["quantity"]
            pos["realized_pnl"] += (price - avg) * qty - fee
            pos["cost"] -= avg * qty
            pos["quantity"] -= qty
    out = {}
    for sym, pos in book.items():
        qty = pos["quantity"]
        out[sym] = {"quantity": qty, "avg_cost": (pos["cost"] / qty) if qty > 0 else ZERO,
                    "cost_basis": pos["cost"] if qty > 0 else ZERO, "realized_pnl": pos["realized_pnl"]}
    return out
