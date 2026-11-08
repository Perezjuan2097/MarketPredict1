import json
from pathlib import Path
from datetime import datetime, timezone


class CommunityStorage:
    def __init__(self, root):
        self.path = Path(root) / "data" / "comunidad.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self):
        if not self.path.exists():
            return {"posts": [], "votes": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            data.setdefault("posts", [])
            data.setdefault("votes", {})
            return data
        except Exception:
            return {"posts": [], "votes": {}}

    def _write(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, symbol):
        symbol = symbol.upper()
        data = self._read()
        posts = [p for p in data["posts"] if p.get("symbol") == symbol]
        votes = data["votes"].get(symbol, {"bullish": 0, "bearish": 0})
        bullish = int(votes.get("bullish", 0))
        bearish = int(votes.get("bearish", 0))
        total = bullish + bearish
        return {
            "symbol": symbol,
            "sentiment": {
                "bullish": round(bullish / total * 100) if total else 50,
                "bearish": round(bearish / total * 100) if total else 50,
                "votes": total,
            },
            "posts": list(reversed(posts[-30:])),
        }

    def add_post(self, symbol, author, text):
        symbol = symbol.upper().strip()
        author = " ".join((author or "Anónimo").strip().split())[:40] or "Anónimo"
        text = " ".join((text or "").strip().split())[:500]
        if not text:
            raise ValueError("El comentario no puede estar vacío.")
        data = self._read()
        data["posts"].append({
            "id": len(data["posts"]) + 1,
            "symbol": symbol,
            "author": author,
            "text": text,
            "date": datetime.now(timezone.utc).isoformat(),
        })
        self._write(data)
        return data["posts"][-1]

    def vote(self, symbol, direction):
        symbol = symbol.upper().strip()
        if direction not in {"bullish", "bearish"}:
            raise ValueError("Voto no válido.")
        data = self._read()
        votes = data["votes"].setdefault(symbol, {"bullish": 0, "bearish": 0})
        votes[direction] = int(votes.get(direction, 0)) + 1
        self._write(data)
        return self.get(symbol)["sentiment"]
