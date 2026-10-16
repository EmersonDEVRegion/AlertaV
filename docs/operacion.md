# Operación

Qué mirar, cómo diagnosticar y qué palancas hay en producción. Las rutas de
operador piden `Authorization: Bearer <OPERATOR_TOKEN>`.

```bash
API=https://alertav-api.onrender.com/api/v1
AUTH="Authorization: Bearer $OPERATOR_TOKEN"
```

## Salud

| Qué | Dónde |
|---|---|
| ¿Está viva la API? | `GET $API/health` |
| ¿Responden la base y PostGIS? | `GET $API/health/ready` |
| ¿Cada fuente está al día? | `GET $API/collectors/health`: estado por collector y por familia |
| ¿Qué falló y por qué? | `GET $API/collectors/runs` (operador), con el `error` completo |
| Asesores de la base | Supabase → Advisors (seguridad y rendimiento) |

Estados de una corrida (`collector_runs.status`):

| Estado | Qué significa | Qué hacer |
|---|---|---|
| `success` | Leyó y guardó. Las filas fuera de la región o del umbral se cuentan como `excluded`, no como problema. | Nada. |
| `partial` | Rechazó filas que no pudo interpretar, o usó un respaldo. El motivo está en `error`. | Revisar el mapeo de la fuente. |
| `degraded` | Corrió, pero lo que leyó no describe el presente (fuente congelada, cuenta que falta en el canario). | La PWA marca la capa; ver la fuente. |
| `failed` | No pudo leer. | `scripts/check_sources.py` reproduce el error en segundos. |

`collector_runs` guarda 30 días (`COLLECTOR_RUNS_RETENTION_DAYS`); los workers
podan cada 6 horas.

## Rutas de operador

```bash
curl -H "$AUTH" "$API/collectors"                                   # registrados
curl -H "$AUTH" "$API/collectors/runs?limit=20"                     # últimas corridas
curl -H "$AUTH" -X POST "$API/collectors/conaf_incendios/run"       # disparo manual
curl -H "$AUTH" -X POST "$API/collectors/backfill-geocoding"        # reintenta puntos
curl -H "$AUTH" -X POST "$API/incidents/correlate"                  # pasada del motor
curl -H "$AUTH" "$API/moderacion?estado=pendiente"                  # comentarios ciudadanos
curl -H "$AUTH" -X POST "$API/moderacion/<public_id>" \
  -H "Content-Type: application/json" -d '{"decision": "aprobar"}'
```

La lista completa está en [`api.md`](api.md).

## Scripts

Desde `backend/`, con el entorno de desarrollo activo.

| Script | Para qué | ¿Toca la base? |
|---|---|---|
| `scripts/check_sources.py` | Lee CONAF, SENAPRED y las demás fuentes de verdad y avisa si cambió el formato | No |
| `scripts/probar_cruce.py` | Prueba un cruce de calles contra Nominatim y Overpass | No |
| `scripts/osm_cruces.py` | Arma el índice de esquinas desde un extracto de OSM (lo corre el workflow `osm-cruces` cada mes) | No: lo carga el backend desde el release |
| `scripts/replay_cruces.py` | Mide el índice de esquinas contra despachos exportados (cuántos quedan en una esquina, cuánto se mueve el punto) | No |
| `scripts/smoke_test.py` | Pruebas de punta a punta contra un PostGIS real | Sí, una base desechable |
| `scripts/smoke_push.py` | El SQL de los avisos push contra un PostGIS real | Sí, limpia lo que crea |
| `scripts/replay_correlacion.py` | Reproduce días reales de señales con el motor, para calibrarlo | Lee producción; escribe sólo en una base desechable |
| `scripts/generate_vapid_keys.py` | Genera el par VAPID (una sola vez: cambiarlo deja mudas las suscripciones) | No |
| `scripts/sig_bomberos.py` | Regenera `static/geo/cuarteles_valpo.json` desde el SIG de Bomberos | No |
| `scripts/fetch_seismic_hazard.py` | Regenera `static/geo/amenaza_sismica_valpo.json` desde el CSN | No |

## Palancas

| Situación | Palanca |
|---|---|
| La instancia se queda sin memoria | `ENABLE_CORRELATION=0` (o `ENABLE_COLLECTORS=0`, `ENABLE_PUSH=0`) en Render |
| Pausar los avisos sin perder suscripciones | `PUSH_ENABLED=false` |
| Reclamo de GBV por el radar de vehículos | `VITE_VEHICLE_RADAR=off` en Vercel y redeploy |
| Volver a meter la prensa al motor | `CORRELATION_PRENSA=true` |
| El índice de esquinas ubica mal | `CRUCES_LOCALES_ENABLED=false` (vuelve a Nominatim sobre la calle) |
| Las imágenes de Viper ubican mal, o un reclamo por el uso de mosaicos de OSM | `VIPER_ENABLED=false` (queda el índice de esquinas) |
| Rehacer el índice de esquinas ya | Actions → `osm-cruces` → *Run workflow*; los workers lo cargan en menos de 6 h |
| Volver a un solo radio para todas las familias | `CORRELATION_PERFILES=false` |
| Open-Meteo agotó la cuota diaria u horaria | Nada: el clima y la grilla se pausan solos hasta que se renueva |
| Costo de Apify | `.\infra\apify\configurar.ps1 -Auditar` |

## Apify

Dos Tasks del Actor `xquik/x-tweet-scraper`:

* **principal** cada 30 minutos con `within_time: 45m`: sólo lo publicado desde
  la corrida anterior. Una entrega vacía es normal.
* **canario** una vez al día, sin ventana: los últimos tuits de cada cuenta. Es
  la única entrega donde se mide si alguna cuenta dejó de verse.

Ambos avisan a `POST /api/v1/apify/webhook`, que encola; los workers procesan.
Configuración y auditoría de costos: [`infra/apify/README.md`](../infra/apify/README.md).

## Reportes ciudadanos

Un incidente sostenido sólo por vecinos aparece en el mapa cuando lo reportan
`CITIZEN_QUORUM` (3) dispositivos y redes distintos, o cuando otra fuente lo
respalda. El texto no se publica hasta que Gemini (o un operador en
`/moderacion`) lo aprueba. Se rechazan teléfonos, correos, RUT y enlaces.
