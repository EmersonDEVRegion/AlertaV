# Despliegue

Producción a costo cero: **Supabase** (PostgreSQL + PostGIS), **Render** (API y
workers), **Vercel** (PWA), **Apify** (despachos de Bomberos en X) y una VM de
**Oracle Cloud** (salida desde Chile para Esval).

## 0. Lo que condiciona todo

**La instancia se duerme.** El plan gratuito de Render apaga el servicio tras
15 minutos sin tráfico HTTP, y con él se apagan los workers: nadie recolecta ni
correlaciona. Ver [§6](#6-mantener-la-api-despierta).

**512 MB y 0,1 vCPU.** Por eso API y workers son dos procesos y no cuatro
(`WORKER_MODE=combined`), y el pool de conexiones es chico. Si aparece
`OOMKilled`, `ENABLE_CORRELATION=0` es el mal menor: la recolección es
irrecuperable, la correlación se pone al día sola.

**Sin disco.** Los logs salen por stdout en JSON. Lo único durable de lo que
pasó es `collector_runs` en la base (30 días).

## 1. Supabase

1. Crear el proyecto (región `sa-east-1`) y guardar la contraseña de la base:
   no se vuelve a mostrar.
2. **Extensiones** (`Database → Extensions`): `postgis`, `btree_gist` y
   `pgcrypto`. La migración `0001` las declara con `IF NOT EXISTS`.
3. **Cadena de conexión** (`Project Settings → Database`): el **session pooler**
   (`aws-0-<región>.pooler.supabase.com:5432`, usuario `postgres.<ref>`). La
   conexión directa sólo resuelve en IPv6 y Render no la alcanza. El pooler de
   transacción (6543) sirve con `DB_DISABLE_PREPARED_STATEMENTS=true`.
4. **Data API**: la app no usa PostgREST. Dejarla apagada evita exponer el
   esquema `public` (PostGIS crea ahí `spatial_ref_sys`).

El esquema lo aplica el contenedor al arrancar (`RUN_MIGRATIONS=1`): si la
migración falla, el contenedor no levanta y Render mantiene el despliegue
anterior. Para hacerlo a mano:

```bash
cd backend
DATABASE_URL="postgresql://postgres.<ref>:<password>@aws-0-<región>.pooler.supabase.com:5432/postgres?sslmode=require" \
  alembic upgrade head
```

## 2. Render

`New → Web Service` desde el repositorio.

| Campo | Valor |
|---|---|
| Runtime | Docker |
| Root Directory | `backend` |
| Dockerfile | `./Dockerfile` |
| Instance | Free |
| Branch | `main`, deploy automático |
| Health Check Path | `/api/v1/health` |

El health check va a `/health` (liveness) y no a `/health/ready`: el segundo
responde 503 si Supabase no contesta, y Render reiniciaría el contenedor en loop
durante una caída de la base.

**Variables:** las de [`backend/.env.production.example`](../backend/.env.production.example).
Todo lo demás tiene valor por defecto en `backend/app/core/config.py`. Los
secretos (`DATABASE_URL`, tokens, claves) van como secretos de Render. Con
`ENVIRONMENT=production` la app no arranca si falta uno obligatorio.

## 3. Vercel

| Campo | Valor |
|---|---|
| Root Directory | `frontend` |
| Framework | Vite |
| `VITE_API_BASE_URL` | `https://alertav-api.onrender.com/api/v1` |

Vite congela las `VITE_*` en el build: cambiar una exige redeploy. Todas son
públicas (`frontend/.env.example`); `vite.config.ts` rechaza el build si una
parece un secreto.

`frontend/vercel.json` define la CSP, las cabeceras de caché y el reenvío de
`/i/:code` a `GET /api/v1/share/i/{code}` (vista previa al compartir). Un
dominio o servicio nuevo que la PWA consulte tiene que entrar en `connect-src`;
el preview local no aplica la CSP.

## 4. Apify

Los despachos de Bomberos y los tuits de @TTIValparaiso entran por
`POST /api/v1/apify/webhook`. Los Tasks, Schedules y webhooks se crean con
`infra/apify/configurar.ps1`; el detalle está en [`infra/apify/README.md`](../infra/apify/README.md).

## 5. Proxy en Chile (Esval)

Esval sólo responde a IP chilenas. Una VM Always Free de Oracle Cloud corre
tinyproxy y sólo deja pasar a Esval desde la salida de Render:
[`infra/proxy-cl/README.md`](../infra/proxy-cl/README.md). La URL va en
`ESVAL_PROXY_URL`.

## 6. Mantener la API despierta

Un ping externo a `https://alertav-api.onrender.com/api/v1/health` cada 10
minutos. Tiene que entrar por el edge de Render: un ping desde el mismo
contenedor no cuenta.

* **Monitor externo** (cron-job.org o UptimeRobot, gratis): el que de verdad
  sostiene la recolección. Timeout de 30 s; el primer ping tras dormir puede
  fallar mientras arranca, pero igual lo despierta. Que avise por correo tras
  3 fallos seguidos.
* **`.github/workflows/keepalive.yml`**: respaldo. GitHub no lo corre puntual
  (a veces cada varias horas) y lo apaga tras 60 días sin actividad en el repo.

## 7. Verificación

```bash
API=https://alertav-api.onrender.com/api/v1
curl $API/health                       # {"status":"ok", ...}
curl $API/health/ready                 # base y PostGIS
curl $API/collectors/health            # cada collector en ok
curl -i -X OPTIONS "$API/incidents/active" \
  -H "Origin: https://alertav.vercel.app" -H "Access-Control-Request-Method: GET"
```

En el log de Render, al arrancar: `procesos supervisados: 2` (API y workers).
Si dice 3 o más, `WORKER_MODE` quedó en `split`.

## 8. Diagnóstico

| Síntoma | Causa probable |
|---|---|
| `exec ./start.sh: no such file or directory` | `start.sh` con CRLF. `.gitattributes` lo evita; si pasó: `git add --renormalize .` |
| `connection refused` hacia Supabase | Conexión directa (IPv6). Usar el session pooler. |
| `prepared statement "asyncpg_stmt_N" does not exist` | Pooler de transacción sin `DB_DISABLE_PREPARED_STATEMENTS=true`. |
| `function postgis_version() does not exist` | Falta la extensión o `extensions` en el `search_path` del rol. |
| «blocked by CORS policy» en el navegador | El origen no coincide **exacto** con `CORS_ORIGINS`: esquema, subdominio, sin barra final. |
| `OOMKilled` | `WORKER_MODE` en `split`, o apagar un motor con `ENABLE_*=0`. |
| La app no arranca y el log nombra una variable | Validación de producción: un secreto vacío o con placeholder. |
| Los datos se congelan de noche | La instancia se durmió: falta el ping del §6. |
| `esval_cortes_agua` en `failed` | `ESVAL_PROXY_URL` ausente, o la VM de Oracle caída. |
| Un collector en `degraded` | Corrió pero no ve el presente; el motivo está en `/collectors/health`. |

## 9. Límites conocidos

* Un solo contenedor sin réplicas: un redeploy interrumpe API y recolección.
* Recolección, correlación y avisos comparten proceso por memoria, no por
  diseño. Con presupuesto, el paso natural es un servicio `web` y otro `worker`.
* La recolección depende del ping externo mientras el plan sea gratuito.
