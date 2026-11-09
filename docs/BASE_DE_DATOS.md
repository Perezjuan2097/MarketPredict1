# Base de datos (Supabase / PostgreSQL)

El esquema está definido en `app/models/orm.py` (18 tablas: usuarios, activos, ejecuciones de modelos, predicciones,
evaluaciones, publicaciones, comentarios, votos, reacciones, seguidores, reportes, notificaciones, alertas, watchlist,
portafolios, operaciones simuladas, auditoría y estado de proveedores). Claves foráneas, índices, restricciones
`CHECK` (por ejemplo un solo voto por usuario y publicación, no seguirse a uno mismo, direcciones y estados válidos)
y campos de auditoría (`created_at`, `updated_at`) están declarados ahí.

> **Estado:** el esquema pasa sus pruebas en SQLite (`tests/test_optional.py`), pero **no se ha probado contra tu
> Supabase**. La aplicación todavía NO lee ni escribe en la base de datos; ese cableado se hará tras verificar estos pasos.

## Pasos (una sola vez)
1. **Cadena de conexión.** En Supabase: botón **Connect → Session pooler**. Defínela como variable de entorno:
   - Windows (PowerShell): `$env:DATABASE_URL="postgresql://postgres.xxxx:CLAVE@aws-0-REGION.pooler.supabase.com:5432/postgres"`
   - Render: variable `DATABASE_URL` del servicio.
   Si tu clave tiene caracteres como `@`, `#` o `/`, cámbiala por una solo con letras y números.
2. **Comprobar la conexión:** `python scripts/verify_install.py` (línea "Supabase/PostgreSQL") o `/api/health`.
3. **Crear la migración inicial (versionada con Alembic):**
   ```
   alembic revision --autogenerate -m "esquema inicial"
   alembic upgrade head
   ```
   El archivo generado queda en `migrations/versions/`; súbelo a GitHub. Revísalo antes de aplicarlo.
   Alternativa rápida sin migraciones (solo pruebas): `python scripts/init_db.py`.
4. **Seguridad en Supabase:** pega `supabase/extra.sql` en *SQL Editor* y ejecútalo. Activa Row Level Security en todas las
   tablas (la clave pública `sb_publishable_...` no podrá leer ni escribir datos) y añade un *trigger* que impide
   modificar o borrar predicciones públicas ya publicadas. Ese SQL no se ha podido probar en PostgreSQL: léelo antes.
5. **Nunca** ejecutes migraciones destructivas sobre datos existentes: `create_all` y Alembic solo crean lo que falta.

## Siguiente paso de desarrollo
Cuando 1–4 estén verificados: guardar cada ejecución de modelos y cada predicción, evaluarlas automáticamente al
vencer su horizonte y migrar la comunidad (hoy en JSON local) a estas tablas.
