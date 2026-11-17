# Seguridad de MarketPredict · Fase 1C

## Hallazgos y cambios

| Severidad | Hallazgo | Resolución |
|---|---|---|
| Alta | El historial JSON era global y permitía escrituras anónimas. | Lectura y escritura con sesión. El servidor determina el propietario y exige CSRF en escritura. Los registros antiguos sin propietario se conservan fuera de la API. |
| Alta | El panel de Alpaca autorizaba por correo o nombre autodeclarado. | La lista del operador usa IDs internos vigentes. Las cuentas bloqueadas pierden acceso. |
| Alta | Una copia de la cookie firmada seguía siendo válida después del cierre de sesión. | Identificador aleatorio rotado al autenticar y concesión revocable en el servidor. PostgreSQL y SQLite guardan solo su huella. |
| Alta | Un cliente podía falsificar `X-Forwarded-For` para cambiar la clave del límite. | Se acepta la cadena únicamente si el par inmediato pertenece a un CIDR autorizado y se recorre desde la derecha. |
| Media | Publicaciones y votos anónimos, y nombre de autor controlado por el cliente. | Sesión y CSRF. El autor proviene de la cuenta válida y se contabiliza un voto por cuenta y símbolo. |
| Media | `RuntimeError` y otros fallos podían exponer datos de proveedor o SQL. | Respuestas genéricas, mensajes de proveedor controlados y logs con endpoint y clase de error. SQLAlchemy oculta parámetros. |
| Media | El informe escribía en disco y podía incorporar texto no escapado. | Descarga en memoria, nombre limitado y contenido escapado. Los enlaces de noticias se limitan a HTTP y HTTPS. |

## Sesiones, permisos y límites

- Las cuentas mantienen sus credenciales y hashes existentes. Las sesiones viejas
  requieren nuevo inicio de sesión. La concesión de usuario vence a los siete días y
  la de recuperación administrativa a una hora en el servidor. No hay renovación automática.
  La cookie usa `HttpOnly`, `SameSite=Lax` y `Secure` en producción.
- Las mutaciones de cuenta, administración, comunidad, historial y la generación
  de informes comprueban CSRF. `/api/analyze` sigue siendo un cálculo público sin
  escritura persistente. Las operaciones privadas existentes comprueban la cuenta
  en el servidor. Una cartera ajena responde como un recurso no encontrado.
- Una cuenta activa con rol `admin` usa su sesión habitual para entrar a
  `/admin/`. El servidor consulta el rol y estado vigentes. El acceso de
  recuperación con `ADMIN_TOKEN` tiene su propia concesión, sin ID de usuario.
  Cambiar ese secreto invalida las concesiones de recuperación.
- El panel permite consultar cuentas y cambiar el rol o estado de otras cuentas.
  Las operaciones exigen sesión administrativa y CSRF. Las rutas
  `POST /admin/api/users/<id>/block` y `POST /admin/api/users/<id>/role`
  aceptan respectivamente `{"blocked": true}` o `false`, y
  `{"role": "admin"}` o `"user"`. El repositorio revalida al administrador
  y protege al último administrador activo dentro de la misma transacción.
  Bloquear revoca las sesiones de la cuenta. Cambiar el rol conserva la sesión
  y la autorización cambia en la siguiente solicitud.
- `BROKER_USER_IDS` es una lista de IDs de cuentas comprobadas por el operador.
  Revisar esa lista antes de cada restauración o sustitución de la base. Los valores
  de `BROKER_USERS` ya no conceden acceso. La sesión de recuperación conserva
  su acceso al bróker. Una cuenta con rol `admin` se rige también por la lista.
- `TRUSTED_PROXY_CIDRS` está vacía por defecto. Configurar exclusivamente las redes
  de proxies que controlen el encadenamiento de IP y evitar acceso directo a la app.
  Una cabecera malformada o demasiado larga se ignora. Mientras no se configure un
  proxy, las peticiones detrás de él comparten su IP para el límite.
- Los contadores son sincronizados y acotados **por proceso**. No constituyen un
  límite distribuido. Para varios workers, se puede aplicar una cuota común en el
  proxy o usar un almacén compartido con expiración y actualización atómica.
  No se introduce Redis. Configurar un solo escritor para los archivos JSON de
  comunidad e historial hasta migrarlos a persistencia transaccional.
- Los eventos se emiten con nombre, endpoint e IDs internos pertinentes. No incluyen
  contraseñas, tokens, correo, IP ni datos de cartera. El almacenamiento y la retención
  de logs quedan a cargo del operador. No se promete protección absoluta contra
  enumeración por registro, cuya respuesta de alta exitosa sigue siendo distinta.

## Activación inicial de una cuenta administradora

El esquema ya dispone de `public.users.role`. La activación inicial requiere
un cambio de datos autorizado por el operador.

1. Confirma el proyecto de Supabase y comprueba por TLS que la revisión actual es
   `20261117_auth_sessions`. Busca el nombre exacto y contrasta el ID con el
   registro de la cuenta autorizada.

   ```sql
   BEGIN READ ONLY;
   SELECT version_num FROM public.alembic_version;
   SELECT id, role, is_blocked
   FROM public.users
   WHERE username COLLATE "C" = 'Juan_Perez';
   ROLLBACK;
   ```

2. Tras recibir autorización explícita para ese ID, sustituye
   `<ID_CONFIRMADO>` por el entero verificado. Ejecuta el bloque en una única
   transacción. Si la fila falta o está bloqueada, PostgreSQL aborta la operación.
   Una cuenta ya promovida permanece en ese rol.

   ```sql
   BEGIN;
   SET LOCAL lock_timeout = '5s';
   LOCK TABLE public.users IN SHARE ROW EXCLUSIVE MODE;
   DO $activation$
   DECLARE
       account_id integer := <ID_CONFIRMADO>;
   BEGIN
       IF NOT EXISTS (
           SELECT 1 FROM public.users
           WHERE id = account_id AND username COLLATE "C" = 'Juan_Perez'
             AND NOT is_blocked AND role IN ('user', 'admin')
       ) THEN
           RAISE EXCEPTION 'La cuenta confirmada no está disponible para activación';
       END IF;

       UPDATE public.users
       SET role = 'admin'
       WHERE id = account_id AND role = 'user';
   END;
   $activation$;
   COMMIT;
   ```

3. Comprueba en solo lectura que ese ID tiene rol `admin` y sigue activo.
   Registra el ID y la hora de la intervención en el registro operativo.
   Con la versión que integra roles publicada, recarga MarketPredict y abre
   **Administración** desde el menú de la cuenta. La sesión habitual se conserva.

## Dependencias

La revisión del entorno Windows de referencia consultó los avisos de PyPI para
los 57 paquetes Python instalados y no recibió avisos activos. Chart.js sigue
fijado a 4.5.0. La [página de avisos del proyecto](https://github.com/chartjs/Chart.js/security/advisories)
no listaba avisos publicados al momento de la revisión. Esto deja fuera el
sistema operativo, las bibliotecas nativas incluidas en wheels y la imagen real
de despliegue. Repite la revisión sobre el entorno que vaya a publicarse.

## Migración

La revisión `20261117_auth_sessions` continúa `20261116_identity_repair`. Añade
únicamente `auth_sessions`, su referencia a usuarios e índices. No toca los hashes
de cuentas ni el motor de pronóstico. En PostgreSQL habilita RLS en la tabla nueva.
Verifica que el rol del backend pueda leer y escribir en ella con una política o con
la propiedad apropiada. No expongas la tabla a clientes anónimos. Una cuenta eliminada
revoca sus concesiones a través de la clave foránea.

En una copia desechable ejecuta `alembic current -v` y `alembic upgrade head`
y comprueba que una segunda ejecución sea idempotente. Prueba registro, login,
logout, bloqueo y lectura de una cartera. El [procedimiento de respaldo y
recuperación](RENDER_REGRESIONES.md) aplica antes de cualquier ensayo real.
El operador prepara y verifica una restauración antes de pedir autorización de
producción. CI opera únicamente sobre bases desechables.

El historial JSON sigue siendo una vista heredada. La clave de propietario incluye
elementos de identidad de la cuenta para evitar que la reutilización de un ID
numérico herede registros. Si se introduce rotación de contraseñas, migrar estas
entradas y votos a una identidad estable antes de habilitar ese flujo.
