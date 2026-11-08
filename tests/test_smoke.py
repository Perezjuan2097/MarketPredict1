from app.services.data_service import DemoFuente, ValidadorDatos, ProcesadorDatos
from app.services.prediction_service import ModeloML

def test_pipeline():
    d=ProcesadorDatos().procesar(ValidadorDatos().validar(DemoFuente().obtener("AAPL",60)))
    p=ModeloML().predecir(d,5,"test")
    assert len(d)==60
    assert len(p)==5
    assert p[0]["valor_estimado"]>0
