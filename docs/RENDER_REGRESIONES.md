# Recuperación de las regresiones de Render

## Pronósticos y calendarios

El histórico diario y `quoteSummary` son peticiones distintas. En Render, el histórico
podía llegar aunque la búsqueda o la ficha fallaran con HTTP 401 o 429. El flujo
antiguo perdía el código de bolsa y lo interpretaba como calendario desconocido.

Los pronósticos usan el metadato del histórico vinculado al símbolo, seguido de
la búsqueda coincidente y de un catálogo explícito para NVDA y AAPL. Sus mercados
primarios constan en las fichas oficiales de [NVDA](https://www.nasdaq.com/market-activity/stocks/nvda)
y [AAPL](https://www.nasdaq.com/market-activity/stocks/aapl). El catálogo debe revisarse
si cambia su cotización primaria. Un mercado explícito en el histórico prevalece
sobre el catálogo. La ficha Yahoo se consulta cuando aún falta el mercado y se
acepta si identifica exactamente el símbolo. Una coincidencia difusa por sí sola
no sustituye al instrumento solicitado.

La API distingue `provider_unavailable` (503), `provider_mismatch` (502),
`unverified_instrument` (422) y `unsupported_calendar` (422). Una respuesta vacía
o una excepción ambigua del histórico no demuestra que el símbolo sea inexistente.
La resolución jamás genera cierres sustitutos. El corte, las sesiones válidas,
el margen poscierre y la evaluación exacta de Etapa 1B siguen aplicándose.

## PostgreSQL existente

La excepción `UndefinedColumn` indica que el ORM nuevo leyó un esquema anterior
a las claves normalizadas. `create_all` no altera tablas existentes. El endpoint
`/api/health` comprueba las columnas y los índices de identidad. Si faltan, las
rutas de cuentas quedan temporalmente indisponibles con HTTP 503.

### Inspección y respaldo

1. Identifica la base y el esquema activos del servicio y confirma con qué rol
   se ejecutarán los comandos. Usa una conexión directa o un pooler de sesión
   con permisos de migración. Configura credenciales fuera del historial de la
   terminal. `DATABASE_URL` corresponde a Alembic y las herramientas nativas
   pueden usar las variables `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER` y un
   archivo de contraseñas administrado por el operador.
2. Revisa `alembic current -v`, `alembic heads` y las columnas e índices de
   `users` y `portfolios`. Si aparece una revisión desconocida, conserva el
   historial y resuelve su procedencia antes de continuar. Registra conteos de
   usuarios, portafolios, predicciones y operaciones. Evita incluir filas o
   hashes de contraseñas en logs.
3. Con los escritores detenidos, crea un respaldo en un destino protegido.
   Comprueba la salida de cada comando y documenta el hash del archivo.

   ```sh
   pg_dump --format=custom --file=marketpredict-pre-identidad.dump
   pg_restore --list marketpredict-pre-identidad.dump
   pg_restore --exit-on-error --no-owner --dbname=marketpredict_restore_test marketpredict-pre-identidad.dump
   ```

   Crea previamente la base desechable de restauración. Verifica integridad,
   conteos, accesos y dependencias del entorno, incluidas políticas y roles.
   El listado del dump por sí solo no valida la restauración.

### Ensayo, autorización y despliegue manual

1. Apunta Alembic a la copia restaurada y ejecuta `alembic upgrade head`. Si la
   base carece de historial, la revisión inicial reconcilia el esquema existente.
   Si ya figura `20261115_identity_keys`, la siguiente revisión verifica y repara
   las claves de PostgreSQL. El destino es `20261116_identity_repair`. Una base
   vacía requiere inicialización aparte, según la guía de base de datos.
2. La revisión usa la misma normalización Python que el repositorio. Busca
   colisiones globales de usuario/correo y de nombre de portafolio por cuenta.
   Las colisiones se informan por ID y detienen la actualización. Solicita una
   decisión al responsable de los datos. Mantén el respaldo y repite el ensayo.
3. En PostgreSQL se añaden columnas faltantes, se completan claves, se endurece su
   nulabilidad y se crean los índices que falten. Las definiciones incompatibles
   detienen la revisión transaccional. La espera de bloqueo es de cinco segundos.
   El tiempo de mantenimiento depende del tamaño de las tablas. Si existen claves
   parciales incorrectas con índices previos, una colisión durante el backfill
   también revierte la transacción y necesita revisión manual.
4. Repite `alembic upgrade head` y comprueba revisión, conteos, inicio de sesión,
   propiedad de portafolios y acceso a predicciones. Los ensayos automatizados
   usan PostgreSQL con usuarios, hashes ficticios, carteras y predicciones
   enlazadas. Verifican que se preservan los identificadores y los OID de tablas.
5. Detente para pedir autorización de producción cuando el respaldo y el ensayo
   estén comprobados. Tras la aprobación, mantén detenidos los escritores durante
   la migración. Despliega manualmente el commit aprobado en Render tras verificar
   el esquema y comprueba `/api/health`, el acceso de una cuenta existente y una
   solicitud de pronóstico. El flujo de CI no despliega ni migra producción.

Si falla la migración PostgreSQL, verifica que la transacción se revirtió y
conserva el respaldo para diagnóstico. Si se necesita revertir una actualización
ya aplicada, prueba primero el artefacto anterior con el esquema actualizado.
Si requiere restauración, recupera el respaldo verificado en una base nueva,
comprueba integridad y vuelve a apuntar el servicio con autorización. Concilia
las escrituras efectuadas después del respaldo antes de cambiar de base.
