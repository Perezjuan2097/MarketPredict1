# Cuentas, predicciones guardadas y portafolio simulado

## Qué hay
- **Registro / inicio de sesión / cierre de sesión** (`/api/auth/*`): contraseñas con hash (scrypt), sesión por cookie
  `HttpOnly`/`SameSite=Lax` (`Secure` en producción), CSRF por cabecera `X-CSRF-Token`, límite de intentos de acceso.
- **Mis predicciones** (`/api/me/predictions`): publicar una predicción (el precio de referencia lo fija el servidor),
  guardar las predicciones de los modelos ("Guardar predicciones" en el panel de modelos), invalidar mientras estén pendientes
  y solicitar evaluación al abrir Mi cuenta o pulsar **Actualizar resultados**. Sólo se evalúa cuando existe el cierre de la sesión objetivo. Se compara con la referencia "mantener el último precio". La evaluación periódica sigue pendiente.
  Una predicción publicada **no se puede editar**; el historial se conserva.
- **Portafolio simulado** (`/api/me/portfolios`): dinero virtual, comisión configurable, compras y ventas al último cierre,
  costo promedio, ganancia realizada/no realizada, pesos y rentabilidad. Sin ventas en corto, una divisa por portafolio y sin
  conexión a ningún bróker.

## Activarlo (requiere la base de datos)
Sin `DATABASE_URL` los endpoints responden 503 "La base de datos no está configurada" y el resto de la app funciona igual.
1. En Render añade `DATABASE_URL` (cadena **Session pooler** de Supabase, con tu contraseña nueva) y `AUTO_CREATE_TABLES=true`.
2. Redespliega. Al primer uso se crean las tablas que falten (nunca se borra ni se modifica nada) y, en PostgreSQL, se activa
   Row Level Security en todas, para que la clave pública de Supabase no pueda leer los datos.
3. Abre `/api/health`: `db.status` debe ser `ok`. Después regístrate desde el botón superior.
4. Recomendado: ejecuta además `supabase/extra.sql` en el editor SQL de Supabase (bloquea editar/borrar predicciones publicadas).
5. Para versionar el esquema en lugar de `AUTO_CREATE_TABLES`, usa Alembic (`docs/BASE_DE_DATOS.md`).

## Cómo se verificó
La lógica (registro, sesiones, CSRF, evaluación, portafolio, aislamiento entre usuarios) se probó con un repositorio en
memoria (`tests/test_account.py`). La capa SQL (`app/repositories/sql.py`) se comprueba con `tests/test_repository_contract.py` en SQLite y PostgreSQL de prueba.

## Limitaciones actuales
- Sin recuperación de contraseña ni verificación de correo (requieren un servicio de envío de correo).
- La comunidad (comentarios y votos) sigue en JSON local; migrarla a estas tablas es el siguiente paso.
- Aún no hay feed público de predicciones, rankings ni moderación; `is_public` ya se guarda pero no se muestra.
- Las predicciones de modelos guardadas son privadas. Consultar el pronóstico no lo guarda. El botón envía el token de la emisión mostrada. Guardar la misma emisión otra vez devuelve las predicciones ya registradas. Ver [Emisiones](ENTERPRISE_2B2.md).
- **Seguimiento** usa un historial JSON separado (`POST /api/save`, `GET /api/history`). Guarda el primer pronóstico del análisis y queda fuera de la evaluación SQL.
- Los precios vienen de Yahoo Finance (último cierre); si Yahoo no responde, la operación o la evaluación se aplaza.
