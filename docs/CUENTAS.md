# Cuentas, predicciones guardadas y portafolio simulado

## Qué hay
- **Registro / inicio de sesión / cierre de sesión** (`/api/auth/*`): contraseñas con hash (scrypt), sesión por cookie
  `HttpOnly`/`SameSite=Lax` (`Secure` en producción), CSRF por cabecera `X-CSRF-Token`, límite de intentos de acceso.
- **Mis predicciones** (`/api/me/predictions`): publicar una predicción (el precio de referencia lo fija el servidor),
  guardar las predicciones de los modelos ("Guardar predicciones" en el panel de modelos), invalidar mientras estén pendientes
  y **evaluación automática** cuando vence el horizonte (compara con el cierre real y con la referencia "mantener el último precio").
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
memoria (`tests/test_account.py`). La capa SQL (`app/repositories/sql.py`) **no se pudo ejecutar** en el entorno de desarrollo:
ejecuta `python -m unittest tests.test_repository_contract -v` en tu equipo; las mismas pruebas de contrato validan ambos repositorios.

## Limitaciones actuales
- Sin recuperación de contraseña ni verificación de correo (requieren un servicio de envío de correo).
- La comunidad (comentarios y votos) sigue en JSON local; migrarla a estas tablas es el siguiente paso.
- Aún no hay feed público de predicciones, rankings ni moderación; `is_public` ya se guarda pero no se muestra.
- Las predicciones de modelos guardadas hoy son del usuario que las guarda (no públicas).
- Los precios vienen de Yahoo Finance (último cierre); si Yahoo no responde, la operación o la evaluación se aplaza.
