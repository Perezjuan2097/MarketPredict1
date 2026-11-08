import json
import os
import tempfile
import threading
from pathlib import Path

_lock = threading.Lock()
FIELDS = {"symbol": str, "fecha": str, "horizonte": str, "prediccion": (int, float), "modelo": str, "version": str}


class Storage:
    """Historial provisional en JSON. Se sustituye por la tabla `predictions` al migrar a Supabase."""

    def __init__(self, root):
        self.path = Path(root) / "data" / "predicciones.json"
        self.path.parent.mkdir(exist_ok=True)

    def read(self):
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []

    @staticmethod
    def validar(registro):
        if not isinstance(registro, dict):
            raise ValueError("Registro inválido.")
        clean = {}
        for key, typ in FIELDS.items():
            value = registro.get(key)
            if not isinstance(value, typ) or isinstance(value, bool):
                raise ValueError(f"Campo inválido o ausente: {key}.")
            clean[key] = value[:60] if isinstance(value, str) else float(value)
        if clean["prediccion"] <= 0:
            raise ValueError("La predicción debe ser positiva.")
        clean["real"] = None
        return clean

    def add(self, registro):
        """Devuelve False si ya existía un registro idéntico (se ignora el duplicado)."""
        clean = self.validar(registro)
        with _lock:
            data = self.read()
            key = {k: v for k, v in clean.items() if k != "real"}
            if any({k: r.get(k) for k in key} == key for r in data):
                return False
            data.append(clean)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)  # escritura atómica
        return True

    def all(self):
        return list(reversed(self.read()))
