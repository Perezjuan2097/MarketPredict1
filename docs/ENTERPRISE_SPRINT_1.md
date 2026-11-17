# MarketPredict Enterprise · Sprint 1

## Alcance

El portal público y el historial personal conservan sus rutas y propiedad. **Empresas** abre un espacio para crear y seleccionar organizaciones, consultar la configuración y administrar miembros existentes. En móvil también está disponible desde el menú de cuenta. El espacio aún no contiene modelos, datasets, predicciones privadas, exportaciones ni operaciones masivas.

El servidor obtiene el usuario de la sesión revocable. Resuelve el slug y consulta una membresía de la organización seleccionada con la cuenta desbloqueada. Aplica el permiso en el repositorio. Los IDs y slugs que recibe el servidor sólo identifican recursos. Las operaciones empresariales no utilizan el rol de administrador global ni ADMIN_TOKEN. Un slug ajeno, suspendido o inexistente responde 404. Una membresía vigente con permiso insuficiente responde 403.

## Permisos

| Operación | owner | admin | member | viewer |
|---|---|---|---|---|
| Consultar resumen y configuración | Sí | Sí | Sí | Sí |
| Consultar directorio con nombre de usuario, rol y estado de bloqueo | Sí | Sí | Sí | — |
| Incorporar, cambiar o revocar `member` y `viewer` | Sí | Sí | — | — |
| Incorporar, cambiar o revocar `owner` y `admin` | Sí | — | — | — |
| Actualizar configuración | Sí | Sí | — | — |

Cualquier cuenta autenticada y desbloqueada puede crear una organización con slug disponible. La creación guarda organización, membresía de propietario y configuración inicial en una transacción. Para incorporar un miembro se requiere su nombre de usuario exacto según la normalización vigente. El directorio queda limitado a la organización y no devuelve correo, contraseña o información financiera. Revocar elimina la membresía. Una nueva solicitud comprueba la autorización actual aunque la sesión siga válida.

Un propietario activo tiene rol `owner` y cuenta desbloqueada. PostgreSQL serializa cambios de membresía, configuración y bloqueos de cuentas con el mismo bloqueo de escritura de la tabla `users`. SQLite utiliza `BEGIN IMMEDIATE`. La memoria usa el bloqueo de repositorio. Contar propietarios y revisar al actor tras adquirir el bloqueo impide que dos solicitudes dejen a la organización sin propietario. Una revocación tampoco puede combinarse con un bloqueo global para dejarla sin propietario. El SQL externo requiere sus propios controles operativos. La FK restringe el borrado de usuarios con membresías. Un GET que ya estaba en curso puede haber consultado su autorización justo antes de la revocación. Las solicitudes posteriores usan el estado vigente.

## API

Base `/api/me/organizations`. Todas las rutas requieren sesión, limitación de solicitudes y `Cache-Control: no-store`. Las mutaciones requieren CSRF. Las listas admiten `after` (ID exclusivo) y `limit` (1–100, por defecto 50).

| Método y sufijo | Entrada / respuesta |
|---|---|
| `POST /` (sin barra final) | `name` y `slug`. Devuelve `organization`, 201. Slug ASCII normalizado a minúsculas y único. |
| `GET /` (sin barra final) | `organizations` propias y activas, con rol y permisos efectivos. |
| `GET /<slug>` | `organization`. |
| `GET /<slug>/members` | `members` de la organización. |
| `POST /<slug>/members` | `username` y `role`. Devuelve `member`, 201. |
| `PATCH /<slug>/members/<user_id>` | `role`. Devuelve `member`. |
| `DELETE /<slug>/members/<user_id>` | Revoca membresía, 204. |
| `GET /<slug>/settings` | `settings` con `timezone` y `currency`. |
| `PATCH /<slug>/settings` | Zona IANA y/o código de tres letras para moneda. Devuelve `settings`. |

Errores: 400 para entradas inválidas, 401 para sesión ausente, 403 para CSRF/permisos insuficientes, 404 para recursos fuera del alcance y 409 para duplicados o transiciones incompatibles.

## Migración y permisos SQL

`20261119_organizations` sigue a `20261118_forecast_saves`. Añade `organizations`, `organization_memberships` y `organization_settings`, con claves y restricciones. Habilita RLS en PostgreSQL sin conceder políticas a roles consumidores. La aplicación actúa como rol propietario en las pruebas locales y filtra cada acceso en el servidor. Un rol PostgreSQL normal, incluso con permisos de tabla, quedó sin visibilidad y sin capacidad para actualizar filas en la prueba local de RLS. El rol de la aplicación desplegada debe comprobarse explícitamente antes de intervenir: propietario o mecanismo de acceso backend aprobado, privilegios para DML, secuencias y el modo de bloqueo usado sobre `users`. La prueba local no acredita la identidad ni los permisos efectivos de producción.

En una futura intervención autorizada revisar `alembic current`, preparar respaldo y ensayar `python -m alembic upgrade 20261119_organizations` en una copia. Comprobar restricciones, RLS, acceso del rol de aplicación y ausencia de acceso de clientes directos. El downgrade bloquea las tablas y se niega a retirarlas si contienen datos. Con datos existentes, revertir el código y conservar las tablas. La revisión anterior de emisiones también conserva el historial en una reversión.

## Cierre de emisiones 2B.2

La firma delimita una emisión por SHA-256 de los resultados y su procedencia canónica. El token vence al límite más temprano entre una hora desde el corte, el límite temporal del motor y el cierre objetivo. La copia personal y su registro de idempotencia se retienen según la política de la cuenta. No hay recolección automática. La copia previa puede recuperarse con un token válido en firma aunque haya vencido. Rotar la clave invalida los tokens previos. Las réplicas comparten la clave de aplicación. La prueba de concurrencia utiliza dos procesos Python con conexiones PostgreSQL independientes y verifica que ambos obtienen los mismos IDs y que existe un solo lote. La autorización y las escrituras siguen separadas de los datos empresariales. Ver [Fase 2B.2](ENTERPRISE_2B2.md).

## Límites y siguiente revisión

El bloqueo PostgreSQL compartido es deliberadamente amplio. Medir contención antes de sustituirlo por bloqueos de menor alcance. La interfaz incorpora directamente cuentas existentes por nombre de usuario. Las invitaciones, aceptación y cuotas por organización pueden añadirse después. Las futuras búsquedas, exportaciones, tareas y operaciones masivas deben comprobar la membresía, filtrar por organización en base de datos, usar claves compuestas para relaciones privadas y probar revocación y acceso cruzado. No reutilizar una emisión pública como autorización de un recurso empresarial. Las futuras políticas RLS por tenant deben ensayarse con el pool y el rol efectivos antes de depender de ellas.
