# Base de datos (Supabase / PostgreSQL)

El esquema está definido en `app/models/orm.py` (18 tablas: usuarios, activos, ejecuciones de modelos, predicciones,
evaluaciones, publicaciones, comentarios, votos, reacciones, seguidores, reportes, notificaciones, alertas, watchlist,
portafolios, operaciones simuladas, auditoría y estado de proveedores). Claves foráneas, índices, restricciones
`CHECK` (por ejemplo un solo voto por usuario y publicación, no seguirse a uno mismo, direcciones y estados válidos)
y campos de auditoría (`created_at`, `updated_at`) están declarados ahí.

> **Estado:** las cuentas, las predicciones privadas y los portafolios usan el repositorio SQL
> cuando existe `DATABASE_URL`. Las pruebas de contrato pueden ejecutarse en SQLite y PostgreSQL.
> La revisión de identidad actualiza esquemas existentes de fase3. La creación inicial mediante
> Alembic se mantiene como trabajo pendiente.

## Pasos (una sola vez)
1. **Cadena de conexión.** En Supabase: botón **Connect → Session pooler**. Defínela como variable de entorno:
   - Windows (PowerShell): `$env:DATABASE_URL="postgresql://postgres.xxxx:CLAVE@aws-0-REGION.pooler.supabase.com:5432/postgres"`
   - Render: variable `DATABASE_URL` del servicio.
   Si tu clave tiene caracteres como `@`, `#` o `/`, cámbiala por una solo con letras y números.
2. **Comprobar la conexión:** `python scripts/verify_install.py` (línea "Supabase/PostgreSQL") o `/api/health`.
3. En una base de desarrollo vacía, ejecuta `python scripts/init_db.py`.
   Tras verificar que se creó el esquema con las claves de identidad de esta versión, registra
   la revisión con el siguiente comando.
   ```
   alembic stamp 20261115_identity_keys
   ```
   Para una base existente de fase3, especialmente en Render, usa el
   [procedimiento de inspección, respaldo y reparación](RENDER_REGRESIONES.md).
4. **Seguridad en Supabase:** pega `supabase/extra.sql` en *SQL Editor* y ejecútalo. Activa Row Level Security en todas las
   tablas (la clave pública `sb_publishable_...` no podrá leer ni escribir datos) y añade un *trigger* que impide
   modificar o borrar predicciones públicas ya publicadas. Ese SQL no se ha podido probar en PostgreSQL: léelo antes.
5. Revisa y ensaya cada revisión sobre una copia antes de aplicarla a una base con datos.

## Actualización de identidad desde fase3

- Los IDs enteros siguen identificando las cuentas y sus relaciones. El login acepta nombre de
  usuario o correo, eliminando espacios exteriores y convirtiendo a minúsculas Unicode con Python.
  El registro mantiene su formato de usuario ASCII de 3–30 caracteres. Los nombres visibles
  conservan su grafía. El correo nuevo se almacena normalizado, igual que en el registro anterior.
- Nombre de usuario y correo tienen unicidad global. El nombre del portafolio es único dentro de
  su cuenta. Las claves se comparan por su valor normalizado, incluso con acentos. Los índices
  únicos protegen escrituras concurrentes. Las integraciones que escriban directamente en SQL
  deben mantener también las claves normalizadas. La escritura habitual pasa por el repositorio.
- En una base existente creada por fase3, haz una copia y detén los escritores. Ensaya en una
  copia `alembic upgrade 20261115_identity_keys`. La revisión busca colisiones antes de cambiar
  el esquema y, si encuentra alguna, informa sus IDs sin escoger ganadores. Resuelve esas
  colisiones con el dueño de los datos y repite el ensayo. Conserva IDs, valores visibles y
  relaciones. `AUTO_CREATE_TABLES` no actualiza tablas existentes.
- Aplica la revisión antes de iniciar el código nuevo. Para revertir, restaura la copia previa.
  Para SQLite de desarrollo, usa una copia sin otros escritores y con `PRAGMA foreign_keys`
  desactivado durante la revisión, ya que Alembic reconstruye esas dos tablas. Verifica
  `PRAGMA foreign_key_check` después de la actualización. PostgreSQL aplica los cambios
  de esquema dentro de su transacción.

## Siguiente paso de desarrollo
Cuando 1–4 estén verificados: guardar cada ejecución de modelos y cada predicción, evaluarlas automáticamente al
vencer su horizonte y migrar la comunidad (hoy en JSON local) a estas tablas.
