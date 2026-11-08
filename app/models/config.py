from dataclasses import dataclass, asdict
import os


@dataclass
class ModelConfig:
    window: int = 20
    horizon: int = 5
    alert_drop: float = 2.0
    model_version: str = "ML-Linear-1.0"

    @classmethod
    def from_env(cls, overrides=None):
        o = overrides or {}
        return cls(
            window=int(o.get("window", os.getenv("MODEL_WINDOW", "20"))),
            horizon=int(o.get("horizon", os.getenv("MODEL_HORIZON", "5"))),
            alert_drop=float(o.get("alert_drop", os.getenv("ALERT_DROP", "2.0"))),
            model_version=str(o.get("model_version", os.getenv("MODEL_VERSION", "ML-Linear-1.0"))),
        )

    def validate(self):
        if not 5 <= self.window <= 250:
            raise ValueError("La ventana debe estar entre 5 y 250.")
        if not 1 <= self.horizon <= 30:
            raise ValueError("El horizonte debe estar entre 1 y 30 días.")
        if not 0 < self.alert_drop <= 50:
            raise ValueError("El umbral de alerta debe estar entre 0 y 50 %.")
        if not self.model_version or len(self.model_version) > 40:
            raise ValueError("Versión de modelo no válida.")
        return self

    def as_dict(self):
        return asdict(self)
