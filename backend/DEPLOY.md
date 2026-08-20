# AlertaV — Despliegue a producción (costo cero)

Supabase (PostgreSQL + PostGIS) · Koyeb (backend) · Vercel (frontend).

---

## 0. Lo que hay que saber antes de empezar

Tres cosas de la capa gratuita condicionan todo el diseño. Vale la pena leerlas
ahora y no descubrirlas el día que haya un incendio real.

**La instancia se apaga sola.** El Free Instance de Koyeb escala a cero tras
**1 hora sin tráfico HTTP**, y eso no se puede desactivar. Cuando se apaga, se
apagan también los dos workers: nadie recolecta y nadie correlaciona. La
solución está en el [paso 5](#5-mantener-la-instancia-despierta) y es
obligatoria, no opcional. (Render tiene el mismo problema con una ventana peor:
15 minutos.)

**512 MB de RAM y 0.1 vCPU.** En local corren tres procesos; en producción
corren **dos**: uvicorn por un lado, y recolección + correlación compartiendo un
único intérprete (`app/workers.py`). Medido con los procesos ociosos, recién
importados:

| Proceso | RSS en reposo |
|---|---|
| API (uvicorn + FastAPI) | ~69 MB |
| Collectors | ~53 MB |
| Correlación | ~46 MB |

Tres procesos parten de ~168 MB; dos, de ~122 MB. Ese piso es sólo el costo de
importar librerías: el uso real bajo carga es bastante mayor, y por eso las
variables de pool de más abajo son tan conservadoras. Si aun así aparece
`OOMKilled`, `start.sh` trae interruptores para apagar un motor.

**El contenedor no tiene disco persistente.** Los logs sólo existen mientras se
los mira. Todo sale por stdout en JSON y la única copia duradera de lo que pasó
es la tabla `collector_runs` en Supabase.

---

## 1. Supabase — crear la base

### 1.1 Proyecto

Crear el proyecto y **guardar la contraseña de la base en ese momento**:
Supabase no la vuelve a mostrar. Región recomendada: `East US (North Virginia)`,
que es la más cercana a la instancia gratuita de Koyeb en Washington D.C. Cada
salto de región son ~100 ms en cada consulta.

### 1.2 Habilitar las extensiones

`Database → Extensions`, buscar y activar:

| Extensión    | Para qué |
|--------------|----------|
| `postgis`    | Geometrías e índices espaciales. Sin esto no existe la columna `geom` y toda la ingesta falla. |
| `btree_gist` | Índice combinado de geometría + tiempo. |
| `pgcrypto`   | `gen_random_uuid()`. |

Supabase las instala en el schema `extensions`, no en `public`. Es lo correcto y
no requiere cambiar nada: el rol `postgres` ya trae `extensions` en su
`search_path`. La migración `0001` las declara con `IF NOT EXISTS`, así que
encontrarlas ya instaladas no es un problema.

### 1.3 Elegir la cadena de conexión

`Project Settings → Database → Connection string`. Hay tres y **la elección
importa**:

| Opción | Host / puerto | Red | ¿Sirve? |
|---|---|---|---|
| **Session pooler** | `aws-0-<región>.pooler.supabase.com:5432` | IPv4 | ✅ **Usar esta.** |
| Transaction pooler | `aws-0-<región>.pooler.supabase.com:6543` | IPv4 | ⚠️ Sirve, con un ajuste (ver abajo). |
| Direct connection | `db.<ref>.supabase.co:5432` | **Sólo IPv6** | ❌ No. |

La conexión directa quedó sin IPv4 en enero de 2024. Si Koyeb no tiene salida
IPv6 —y no hay garantía de que la tenga— el síntoma es un `connection refused`
que no dice nada sobre IPv6 y se pierden horas buscando en el lugar equivocado.
El pooler responde en IPv4 siempre.

Entre los dos poolers, **session (5432)** es el que corresponde a este backend:
mantiene una conexión de servidor por cada conexión del cliente, que es
exactamente el patrón de tres procesos de larga vida. El pooler de transacción
está pensado para funciones serverless.

> **Si igual usás el de transacción (6543):** poné
> `DB_DISABLE_PREPARED_STATEMENTS=true`. PgBouncer en modo transacción reparte
> las consultas de un mismo cliente entre distintas conexiones de servidor, y
> asyncpg —que cachea sentencias preparadas por conexión— empieza a fallar con
> `prepared statement "asyncpg_stmt_3" does not exist`. El error aparece de
> forma intermitente y bajo carga, que es la peor combinación posible para
> diagnosticar.

La cadena queda así (usuario `postgres.<project-ref>`, no `postgres` a secas):

```
postgresql://postgres.abcdefghijklm:TU_PASSWORD@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require
```

Si la contraseña tiene `@`, `:`, `/` o `#`, hay que percent-encodearla
(`@` → `%40`, `:` → `%3A`). Lo más simple es generar una contraseña
alfanumérica desde el panel y evitar el problema.

### 1.4 Aplicar el esquema

**Desde tu equipo, una sola vez.** No dejes que lo haga el contenedor: un
despliegue automático no debería poder alterar el esquema de producción sin que
nadie lo mire.

> **En Render no hay dónde correr esto.** El plan gratuito no da Shell ni
> Pre-Deploy Jobs. En ese caso poné `RUN_MIGRATIONS=1` en las variables de
> entorno del servicio: `start.sh` ejecuta `alembic upgrade head` antes de
> levantar uvicorn y los workers, y si la migración falla el contenedor **no
> arranca** —que es el comportamiento que se quiere: mejor un deploy fallido y
> visible que una API sirviendo 500s contra un esquema viejo.
>
> Dos consecuencias de dejarlo prendido, para tenerlas presentes:
>
> - Se ejecuta en **cada arranque**, y en Render el free tier se apaga tras 15
>   minutos sin tráfico. Cuando no hay nada que aplicar el costo es una conexión
>   y una consulta a `alembic_version`; con una migración pesada pendiente, en
>   cambio, se suma al tiempo de cold start.
> - Cualquier `git push` a la rama desplegada puede alterar el esquema de
>   producción. Si eso preocupa, la alternativa es prender la variable sólo para
>   el deploy que trae migraciones y volverla a `0` después.
>
> Lo que **no** hay que hacer es mover las migraciones al `lifespan` de FastAPI:
> `migrations/env.py` llama a `fileConfig()`, que desactiva los loggers ya
> configurados por la app; los workers de `start.sh` arrancan en paralelo a
> uvicorn y no esperarían al esquema; y `command.upgrade()` es síncrono, así que
> bloquea el event loop y retrasa la apertura del puerto que Render usa para dar
> el deploy por bueno.

PowerShell:

```powershell
cd backend
$env:DATABASE_URL="postgresql://postgres.<ref>:<password>@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require"
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Verificar en el SQL Editor de Supabase:

```sql
select postgis_version();
select table_name from information_schema.tables where table_schema = 'alertav';
```

Deberías ver `raw_events`, `collector_runs`, `incidents` y las tablas de
correlación.

> Si `postgis_version()` responde `function does not exist`, el `search_path`
> del rol no incluye `extensions`. Se arregla con:
> ```sql
> alter role postgres in database postgres
>   set search_path to "$user", public, extensions, alertav;
> ```

---

## 2. Koyeb — desplegar el backend

`Create Web Service` → GitHub → tu repositorio.

| Campo | Valor |
|---|---|
| Builder | **Dockerfile** |
| Work directory | `backend` |
| Dockerfile path | `Dockerfile` |
| Instance | `Free` |
| Region | `Washington, D.C.` |
| Port | `8000` (protocolo HTTP) |
| Health check | HTTP, path `/api/v1/health` |

El health check apunta a `/api/v1/health` (liveness) y no a `/api/v1/health/ready`
a propósito: el de readiness devuelve 503 si Supabase no responde, y con eso
Koyeb reiniciaría el contenedor en loop durante una caída de la base —cuando lo
que se quiere es que la API siga en pie devolviendo el último estado conocido.
`ready` queda para consultarlo a mano.

---

## 3. Variables de entorno en Koyeb

Marcá como **Secret** las tres primeras; el resto pueden ir como plain text.

### Obligatorias

| Variable | Valor | Nota |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres.<ref>:<pass>@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require` | 🔒 Secret. Tiene prioridad sobre las `POSTGRES_*`. |
| `FIRMS_MAP_KEY` | tu MAP_KEY de NASA FIRMS | 🔒 Secret. Sin esto el collector de FIRMS arranca y falla en cada ciclo. |
| `CORS_ORIGINS` | `https://alertav.vercel.app` | Dominio exacto del frontend. CSV si hay varios. |
| `DB_SSL_MODE` | `require` | Supabase rechaza conexiones sin TLS. |
| `ENVIRONMENT` | `production` | |
| `DEBUG` | `false` | |

### Recomendadas

| Variable | Valor | Por qué |
|---|---|---|
| `CORS_ORIGIN_REGEX` | `^https://alertav-[a-z0-9-]+\.vercel\.app$` | Los previews de Vercel cambian de subdominio en cada rama; enumerarlos es imposible. Ajustá el prefijo al nombre real de tu proyecto. |
| `DB_POOL_SIZE` | `2` | El techo real es `(POOL_SIZE + MAX_OVERFLOW) × 2 procesos`. Con 2/3 son 10 conexiones. |
| `DB_MAX_OVERFLOW` | `3` | |
| `LOG_LEVEL` | `INFO` | |
| `RUN_MIGRATIONS` | `0` | Las migraciones se aplican a mano (paso 1.4). **En Render el valor es `1`**: sin Shell ni Pre-Deploy Jobs, el contenedor es el único lugar donde pueden correr. Si falla, no arranca. |
| `WORKER_MODE` | `combined` | Los dos motores de fondo en un proceso. `split` vuelve al esquema de tres, útil para depurar. |

### Sólo si usás el pooler de transacción (puerto 6543)

| Variable | Valor |
|---|---|
| `DB_DISABLE_PREPARED_STATEMENTS` | `true` |

### Interruptores de emergencia

Si la instancia se queda sin memoria (`OOMKilled` en los logs), apagá un motor
en vez de dejar que el contenedor muera en loop. Funcionan igual en ambos
`WORKER_MODE`:

| Variable | Efecto |
|---|---|
| `ENABLE_COLLECTORS=0` | Sólo API + correlación. Deja de entrar información nueva. |
| `ENABLE_CORRELATION=0` | Sólo API + recolección. Los datos siguen entrando; el mapa deja de agrupar señales en incidentes. |

De los dos, `ENABLE_CORRELATION=0` es el mal menor: la recolección es
irrecuperable —lo que no se capturó de CONAF o FIRMS en su momento ya no está—
mientras que la correlación es idempotente y se pone al día sola cuando vuelve a
encenderse.

### Cadencias — bajar la presión en la capa gratuita

Los valores por defecto están pensados para una instancia decente. Con 0.1 vCPU
conviene espaciarlos; el costo es latencia de detección, no pérdida de datos:

| Variable | Defecto | Sugerido en free |
|---|---|---|
| `CONAF_POLL_INTERVAL_SECONDS` | 300 | `600` |
| `SENAPRED_POLL_INTERVAL_SECONDS` | 600 | `900` |
| `FIRMS_POLL_INTERVAL_SECONDS` | 900 | `1800` |
| `CORRELATION_POLL_INTERVAL_SECONDS` | 120 | `300` |

> **Nada de esto va en Supabase.** El panel de Supabase no recibe variables de
> entorno de la aplicación: sólo entrega la cadena de conexión. Todas las
> variables de arriba se pegan en Koyeb.

---

## 4. Vercel — apuntar el frontend al backend

Hoy `frontend/.env` tiene `VITE_API_BASE_URL=/api/v1`, que funciona en
desarrollo porque Vite hace de proxy. **En producción esa ruta no existe**: le
pega a Vercel, que devuelve 404. Hay que cambiarla.

En `Project Settings → Environment Variables` (entorno Production):

| Variable | Valor |
|---|---|
| `VITE_API_BASE_URL` | `https://<tu-app>.koyeb.app/api/v1` |

Y volver a desplegar — Vite congela las `VITE_*` en tiempo de build, así que
cambiar la variable sin redeploy no hace nada.

> **Alternativa sin CORS:** un `frontend/vercel.json` con
> ```json
> { "rewrites": [{ "source": "/api/:path*", "destination": "https://<tu-app>.koyeb.app/api/:path*" }] }
> ```
> deja `VITE_API_BASE_URL=/api/v1` como está. El navegador ve un solo origen,
> desaparece el preflight y se ahorra un round-trip por carga. A cambio, todo el
> tráfico de datos pasa por el edge de Vercel y consume su cuota. Con el volumen
> de este proyecto es despreciable; queda a tu criterio.

---

## 5. Mantener la instancia despierta

Sin esto el sistema deja de recolectar cada noche. Configurá un ping externo
—[cron-job.org](https://cron-job.org) o UptimeRobot, ambos gratis— a:

```
https://<tu-app>.koyeb.app/api/v1/health
```

cada **10 minutos**. Tiene que ser un servicio externo: el tráfico que cuenta
para el scale-to-zero es el que entra por el edge, así que un ping que el
contenedor se hace a sí mismo no sirve de nada.

Un efecto secundario útil: el mismo monitor te avisa por correo cuando la API
deja de responder.

---

## 6. Verificación

```bash
# 1. La API está viva
curl https://<tu-app>.koyeb.app/api/v1/health

# 2. La base y PostGIS responden — esto es lo que de verdad importa
curl https://<tu-app>.koyeb.app/api/v1/health/ready
#    {"status":"ok","database":true,"postgis":"3.3 USE_GEOS=1 ..."}

# 3. Los collectors están escribiendo (esperá un par de ciclos)
curl "https://<tu-app>.koyeb.app/api/v1/events/stats"

# 4. CORS: simular el preflight del navegador desde el origen de Vercel
curl -i -X OPTIONS "https://<tu-app>.koyeb.app/api/v1/events" \
  -H "Origin: https://alertav.vercel.app" \
  -H "Access-Control-Request-Method: GET"
#    Debe responder 200 con access-control-allow-origin: https://alertav.vercel.app
```

En los logs de Koyeb deberías ver, en los primeros minutos:

```json
{"level":"INFO","logger":"orchestrator","message":"modo combinado: recolección y correlación comparten proceso"}
{"level":"INFO","logger":"orchestrator","message":"procesos supervisados: 2"}
{"level":"INFO","logger":"alertav.workers","message":"workers iniciados","motores":["collectors","correlation"]}
{"level":"INFO","logger":"alertav.runner","message":"runner iniciado","schedule":{...}}
{"level":"INFO","logger":"alertav.correlation","message":"motor de correlación iniciado"}
```

Son **2** procesos supervisados, no 3: es la señal de que el modo combinado está
activo. Si ves 3, `WORKER_MODE` quedó en `split`.

---

## 7. Diagnóstico rápido

| Síntoma | Causa probable |
|---|---|
| `exec /app/start.sh: no such file or directory` | `start.sh` viajó con saltos de línea CRLF. El `.gitattributes` del repositorio lo previene; si ya pasó: `git add --renormalize . && git commit`. |
| `connection refused` hacia Supabase | Estás usando la conexión directa (IPv6). Cambiá al pooler. |
| `prepared statement "asyncpg_stmt_N" does not exist` | Pooler en modo transacción sin `DB_DISABLE_PREPARED_STATEMENTS=true`. |
| `function postgis_version() does not exist` | Falta PostGIS o falta `extensions` en el `search_path` (ver 1.4). |
| El navegador dice "blocked by CORS policy" | El origen de `CORS_ORIGINS` no coincide **exactamente** — `https://` sin barra final, y el subdominio tal cual. |
| `OOMKilled` en los logs | 512 MB no alcanzaron. Verificá que `WORKER_MODE` no esté en `split`; si ya está en `combined`, apagá un motor con `ENABLE_*=0`. |
| `motor caído; se detiene el proceso completo` | Un motor de fondo falló de forma estructural. El proceso sale con código 1 y `start.sh` lo reinicia con backoff; el `error` del log dice cuál y por qué. |
| `WORKER_MODE='...' no es válido` | Sólo se aceptan `combined` y `split`. El contenedor no arranca a propósito, en vez de quedar en un estado a medias. |
| Los datos se congelan de noche y reviven al abrir el mapa | Scale-to-zero. Falta el ping del paso 5. |
| Logs vacíos aunque el servicio corre | Sólo pasa si se perdió `PYTHONUNBUFFERED=1` del Dockerfile. |

---

## 8. Qué queda pendiente para que esto sea producción de verdad

Esta arquitectura es correcta para un MVP con costo cero, y tiene límites que
conviene tener escritos en vez de descubrir:

- **Un solo contenedor sin réplicas.** Un redeploy es una interrupción total, de
  la API y de la recolección.
- **Recolección y correlación comparten proceso.** Es una concesión al límite de
  memoria, no un diseño: un cuelgue del motor de correlación se lleva puesta la
  recolección. La frontera que sí se defendió es la de la API, que sigue
  aislada. El paso natural cuando haya presupuesto son dos servicios —`web` y
  `worker`— y ahí `WORKER_MODE=split` deja de tener sentido porque cada motor
  vuelve a su contenedor.
- **La recolección se detiene si el ping falla.** El keepalive es un parche
  sobre una restricción de la capa gratuita, no una decisión de diseño.
- **Sin alertas.** Hoy la única forma de saber que un collector lleva horas
  fallando es mirar `collector_runs` a mano. La tabla ya guarda todo lo
  necesario para automatizarlo.
