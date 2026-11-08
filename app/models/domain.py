from dataclasses import dataclass, asdict
from typing import Optional

@dataclass
class DatosMercado:
    fecha: str
    apertura: float
    maximo: float
    minimo: float
    cierre: float
    volumen: int
    variacion: float = 0.0

@dataclass
class Indicador:
    nombre: str
    valor: float
    descripcion: str

@dataclass
class Prediccion:
    fecha_generacion: str
    fecha_objetivo: str
    horizonte: str
    valor_estimado: float
    modelo: str
    version: str

@dataclass
class AlertaVenta:
    nivel: str
    fecha: str
    fundamento: str

@dataclass
class RegistroPrediccion:
    activo: str
    fecha: str
    horizonte: str
    prediccion: float
    modelo: str
    version: str
    real: Optional[float] = None

def to_dict(obj):
    return asdict(obj)
