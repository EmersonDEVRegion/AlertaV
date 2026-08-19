# AlertaV — Fire Data Collector

Backend de recolección y correlación de señales de emergencia de la Región de
Valparaíso. Dos hitos completos: **recolectar** señales de fuentes
independientes y **fusionarlas** en incidentes con confianza agregada.

Stack: Python 3.11+ · FastAPI · SQLAlchemy 2.0 async · PostgreSQL + PostGIS · Alembic.

---

## La decisión que ordena todo el diseño

`raw_events` guarda **señales**, no incidentes.

Una anomalía térmica de un satélite, un despacho de radio y un vecino diciendo
"veo humo" son tres observaciones independientes con distinto grado de
credibilidad. Ninguna es, por sí sola, un incendio confirmado. El sistema las
almacena separadas, con su fuente y su confianza, y deja que el motor de
correlación decida cuándo varias señales concordantes constituyen un incidente.

Eso tiene tres consecuencias visibles en el código:

- El collector de FIRMS emite `type = thermal_anomaly`, nunca `wildfire`. Hay un
  test que lo garantiza.
- El GeoJSON de `/events` incluye `is_confirmed_incident: false` en cada feature,
  para que la PWA no pueda pintar una detección satelital como incendio por
  descuido. En `/incidents/geojson` ese campo sí es un dato real del motor.
- La confianza del evento es la de *esa señal*. La del incidente la calcula el
  Confidence Engine agregando señales, y nunca llega a 1.0 sin una fuente que
  haya ido al lugar.

---

## Puesta en marcha

```bash
# 1. Base de datos
docker compose up -d db

# 2. Entorno
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Configuración
cp .env.example .env        # completar POSTGRES_PASSWORD y FIRMS_MAP_KEY

# 4. Esquema
alembic upgrade head

# 5. API
uvicorn app.main:app --reload     # http://localhost:8000/docs
```

La `MAP_KEY` de FIRMS es gratuita: https://firms.modaps.eosdis.nasa.gov/api/map_key/

### Recolección

```bash
python -m app.collectors.runner                          # una pasada de todos
python -m app.collectors.runner --collector conaf_incendios
python -m app.collectors.runner --show-schedule          # cadencia configurada
python -m app.collectors.runner --loop                   # cada uno a su ritmo
python -m app.collectors.runner --loop --interval 900    # forzar cadencia común
```

En modo `--loop` cada collector corre en su propia tarea con su propio intervalo
(`CONAF_POLL_INTERVAL_SECONDS`, `SENAPRED_POLL_INTERVAL_SECONDS`,
`FIRMS_POLL_INTERVAL_SECONDS`). Un incendio de CONAF cambia de estado en minutos;
una pasada de satélite de FIRMS ocurre unas pocas veces al día: forzar una
cadencia común significaría malgastar cuota o llegar tarde. El primer disparo de
cada collector se dispersa aleatoriamente para no golpear todos los servicios
institucionales en el mismo segundo tras un reinicio.

Para la ventana de 7–14 días, el runner corre como proceso aparte de la API: la
recolección no debe competir por los workers que atienden ciudadanos ni caerse
con un redeploy.

### Correlación

```bash
python -m app.services.correlation.runner              # una pasada
python -m app.services.correlation.runner --loop       # worker continuo
python -m app.services.correlation.runner --radius-m 2000 --window-hours 3
```

### Verificación

```bash
pytest                          # 181 tests, sin red ni base de datos
python scripts/check_sources.py # golpea CONAF y SENAPRED de verdad, sin escribir
python scripts/smoke_test.py    # verificaciones e2e contra PostGIS real
ruff check .
```

`check_sources.py` es la primera herramienta que hay que correr cuando
`collector_runs` empiece a mostrar corridas `partial` o `failed`: reproduce el
problema en segundos, con el mensaje completo de la fuente y avisando si
desaparecieron campos que el mapeo da por sentados.

---

## Estructura

```
backend/
├── app/
│   ├── main.py                    # instancia FastAPI, CORS, lifespan
│   ├── core/
│   │   ├── config.py              # settings desde .env (pydantic-settings)
│   │   ├── database.py            # engine y sesión async
│   │   ├── logging.py             # logs JSON estructurados
│   │   └── exceptions.py          # errores de dominio → respuestas HTTP
│   ├── models/
│   │   ├── enums.py               # EventSource, EventType, IncidentType/Status, LinkMethod
│   │   ├── base.py                # DeclarativeBase + convención de nombres
│   │   ├── event.py               # RawEvent, SourceConfidence, CollectorRun
│   │   └── incident.py            # Incident, IncidentEvent, IncidentCounter
│   ├── schemas/
│   │   ├── event.py               # contrato Pydantic de ingesta y salida
│   │   └── incident.py            # contrato de salida del mapa
│   ├── repositories/              # todo el SQL vive aquí
│   │   ├── event_repository.py
│   │   └── incident_repository.py # DBSCAN, ST_DWithin, fusión, advisory lock
│   ├── services/
│   │   ├── ingest_service.py      # única puerta de entrada de datos
│   │   ├── incident_service.py    # lectura de incidentes para el mapa
│   │   └── correlation/
│   │       ├── confidence.py      # Confidence Engine — puro, sin I/O
│   │       ├── communes.py        # Paso B — extracción y matching, puro
│   │       ├── engine.py          # orquestación; lo único que toca la base
│   │       └── runner.py          # worker periódico
│   ├── api/v1/endpoints/          # events, incidents, collectors, health
│   └── collectors/
│       ├── base.py                # plantilla fetch → normalize → upsert
│       ├── registry.py            # collectors disponibles
│       ├── runner.py              # CLI con cadencia por collector
│       ├── geoservices.py         # ArcGIS + WFS + GeoJSON, paginación, respaldos
│       ├── firms/                 # cliente HTTP + mapeo NASA FIRMS
│       ├── conaf/                 # cliente + mapeo incendios CONAF
│       └── senapred/              # cliente + mapeo alertas SENAPRED
├── migrations/versions/
│   ├── 0001_initial_schema.py     # señales
│   └── 0002_incidents.py          # incidentes y correlación
├── sql/                           # el mismo DDL, legible de corrido
├── scripts/
│   ├── smoke_test.py              # e2e contra PostGIS real
│   └── check_sources.py           # chequeo en vivo de las fuentes, sin escribir
└── tests/
```

Los archivos de `sql/` y las migraciones contienen el mismo DDL. El `.sql` existe
para poder leer el modelo de una sentada; el que se ejecuta en cualquier entorno
es Alembic.

---

## El esquema

```sql
alertav.raw_events
  id, public_id
  timestamp     TIMESTAMPTZ   -- momento SEGÚN LA FUENTE, en UTC
  source        ENUM          -- citizen | broadcastify | nasa_firms | conaf | ...
  type          ENUM          -- thermal_anomaly | smoke | wildfire | dispatch | ...
  lat, lon      DOUBLE PRECISION
  geom          geometry(Point,4326) GENERATED ALWAYS AS (...) STORED
  text          TEXT
  external_id   TEXT
  confidence    REAL          -- [0,1]
  raw_data      JSONB         -- payload original íntegro
  commune, province, ingested_at, processed_at, incident_id, created_at, updated_at
```

Decisiones que vale la pena conocer antes de tocar la tabla:

**`geom` es una columna generada, no un campo que se escriba.** Se deriva de
`lat`/`lon` en la base. Coordenada y geometría no pueden divergir nunca, ni
siquiera si alguien inserta por fuera del ORM.

**La idempotencia se apoya en un índice único parcial** sobre
`(source, external_id) WHERE external_id IS NOT NULL`. Reejecutar un collector
sobre la misma ventana no duplica nada. Los reportes ciudadanos no tienen
`external_id` y por eso quedan fuera del índice: dos vecinos reportando el mismo
humo son dos señales distintas, y esa multiplicidad es justamente lo que debe
subir la confianza del incidente.

**`ix_raw_events_geom_timestamp` es un GiST sobre `(geom, timestamp)`**, gracias a
`btree_gist`. Es el índice del motor de correlación: "cerca en el espacio Y en el
tiempo" se resuelve con una sola estructura.

**`raw_data` guarda el payload original completo.** Cuando el algoritmo de
correlación cambie, se reprocesa desde la base sin volver a consultar APIs que
quizá ya no sirvan esos datos.

**`collector_runs` registra cada ejecución.** Sin esa tabla, un hueco en los
datos es ambiguo: ¿no hubo eventos, o el collector estaba caído? Durante la
ventana de calibración esa distinción es exactamente lo que se necesita.

### Incidentes

```sql
alertav.incidents
  id, public_id
  code                  TEXT   -- folio legible: INC-2026-00142
  type                  ENUM   -- possible_fire | wildfire | structural_fire | flood | ...
  status                ENUM   -- active | controlled | extinguished | stale | merged | dismissed
  lat, lon              DOUBLE PRECISION   -- NOT NULL
  geom                  geometry(Point,4326) GENERATED ALWAYS AS (...) STORED
  confidence            REAL   -- confianza en el FENÓMENO
  alert_confidence      REAL   -- confianza en el ESTADO DE ALERTA
  alert_level           TEXT   -- roja | amarilla | temprana_preventiva | verde
  is_official_confirmed BOOLEAN
  confidence_breakdown  JSONB  -- derivación auditable del número
  event_count, source_count, sources TEXT[]
  title, commune, province
  first_seen_at, last_seen_at, resolved_at, correlated_at
  merged_into_id, created_at, updated_at

alertav.incident_events            -- tabla intermedia CON metadatos
  incident_id, raw_event_id        -- PK
  link_method  ENUM                -- spatial | commune_text | manual
  link_confidence REAL             -- cuán seguro es EL VÍNCULO, no la señal
  distance_m, matched_commune, note, linked_at
```

**`lat`/`lon` son NOT NULL, y eso es una decisión, no un descuido.** Un incidente
nace del Paso A, que sólo agrupa señales georreferenciadas. Una alerta de
SENAPRED sin coordenadas se adjunta a un incidente que ya existe o queda sin
vincular — pero nunca inventa un punto en el mapa. Es la continuación directa de
la decisión de la fase anterior de no geocodificar las alertas a la fuerza.

**`confidence` y `alert_confidence` son ejes distintos y no se promedian.** Uno
dice cuán seguros estamos de que hay fuego; el otro, qué declaró el Estado. Un
incidente puede tener alerta roja vigente al 100 % y aun así no estar confirmado
por CONAF, y el mapa debería mostrar ambas cosas por separado.

**El folio lo genera la base.** `alertav.next_incident_code()` incrementa un
contador por año en un `INSERT ... ON CONFLICT DO UPDATE`, que serializa por
bloqueo de fila: dos workers concurrentes nunca reciben el mismo número. Como es
`DEFAULT` de la columna, cualquier inserción —ORM, script o `psql`— recibe uno
consistente. Puede tener huecos si una transacción se revierte; es aceptable,
porque el código es una etiqueta para decir "el INC-2026-00142" por radio, no un
folio contable.

**La tabla intermedia guarda el motivo del vínculo, no sólo el vínculo.** Un
`spatial` es una coincidencia geométrica medible con su `distance_m`; un
`commune_text` es una heurística sobre el nombre de una comuna. Si mañana el
Paso B resulta ruidoso, se borran sólo sus enlaces y se recalcula la confianza
sin tocar el Paso A.

**Y con una asimetría deliberada de cardinalidad:** una señal georreferenciada
pertenece a lo sumo a **un** incidente —lo impone un índice único parcial sobre
`link_method = 'spatial'`— pero una alerta comunal puede pertenecer a **varios**.
Una alerta roja para Viña del Mar cubre de verdad todos los incendios activos de
Viña del Mar; forzarla a elegir uno inventaría una precisión que el acto
administrativo no tiene.

---

## Ingesta

Todo entra por `EventCreate`. Esa es la única frontera donde se normaliza; de ahí
en adelante el dato es confiable.

- `timestamp` se normaliza siempre a UTC. Un naive se interpreta como UTC, no se
  adivina zona local.
- `lat`/`lon` van juntos o no van.
- Un evento sin coordenadas y sin texto se rechaza: no aporta señal
  correlacionable.
- `confidence` es opcional; si falta se aplica la línea base de la fuente.
- Estar fuera del bounding box de Valparaíso **no** invalida el evento por
  defecto (`REJECT_OUTSIDE_REGION=false`). Durante la recolección es preferible
  guardar de más y filtrar después que perder señales limítrofes.

Los reportes ciudadanos entran por `CitizenReportCreate`, que fija `source` y
`confidence` **en el servidor**. Un cliente no puede declararse `conaf` ni
publicar una alerta oficial — hay un test que lo comprueba.

---

## Endpoints

| Método | Ruta | Propósito |
|---|---|---|
| POST | `/api/v1/events` | Ingesta unitaria |
| POST | `/api/v1/events/batch` | Ingesta idempotente por lote (collectors) |
| POST | `/api/v1/events/citizen-report` | Reporte desde la PWA |
| GET | `/api/v1/events` | Listado con filtros de tiempo, fuente, tipo, bbox y radio |
| GET | `/api/v1/events/geojson` | FeatureCollection para MapLibre GL JS |
| GET | `/api/v1/events/stats` | Resumen de la ventana de recolección |
| GET | `/api/v1/events/{public_id}/neighbours` | Señales cercanas en espacio y tiempo |
| GET | `/api/v1/events/seismic` | **Sismos del USGS** con magnitud y profundidad (JOIN con `seismic_details`) |
| GET | `/api/v1/events/seismic/geojson` | Los mismos, como FeatureCollection para la capa sísmica |
| GET | `/api/v1/events/seismic/stats` | Resumen de la ventana sísmica |
| GET | `/api/v1/incidents/active` | **Incidentes consolidados para el mapa** |
| GET | `/api/v1/incidents/geojson` | Los mismos, como FeatureCollection |
| GET | `/api/v1/incidents/stats` | Resumen de la correlación |
| GET | `/api/v1/incidents/{code}` | Detalle con todas sus señales y el motivo de cada vínculo |
| POST | `/api/v1/incidents/correlate` | Disparo manual del motor |
| GET | `/api/v1/collectors` | Collectors registrados |
| POST | `/api/v1/collectors/{name}/run` | Disparo manual |
| GET | `/api/v1/collectors/runs` | Historial de ejecuciones |
| GET | `/api/v1/health/ready` | Readiness (verifica PostGIS, 503 si falta) |

Las rutas `/events/seismic*` van declaradas **antes** de `/events/{public_id}`:
FastAPI resuelve por orden de registro y, puestas después, "seismic" entraría
por la ruta del detalle y fallaría al parsearlo como UUID. Hay un test que lo
fija (`tests/test_seismic_endpoint.py::TestSeismicRouting`).

Su recorte geográfico es `usgs_bbox`, no `region_bbox`. La diferencia es
deliberada: un sismo a 200 km de Valparaíso se siente en Valparaíso, así que
aplicarle el recorte pensado para incendios puntuales borraría del mapa justo
los eventos que explican por qué tembló. Y `magnitude` puede venir en `null`
—el USGS publica la detección antes de terminar de calcularla—, así que ningún
consumidor puede asumir que siempre hay un número.

`/events` expone **señales**; `/incidents` expone **hechos**. La PWA consume la
segunda; la primera queda para calibrar, auditar y depurar.

Un incidente de `/incidents/active` se ve así:

```json
{
  "code": "INC-2026-00142",
  "type": "wildfire",
  "status": "active",
  "lat": -33.02771, "lon": -71.52043,
  "confidence": 1.0,
  "confidence_level": "confirmed",
  "confidence_label": "confirmado",
  "is_official_confirmed": true,
  "alert_level": "roja",
  "alert_confidence": 1.0,
  "title": "Incendio forestal — Viña del Mar",
  "commune": "Viña del Mar",
  "event_count": 7, "source_count": 4,
  "sources": ["citizen", "conaf", "nasa_firms", "senapred"],
  "is_multi_source": true,
  "confidence_breakdown": {
    "policy_version": "2.0.0",
    "by_source": {
      "conaf":      {"signals": 1, "contribution": 1.0,  "ceiling": 1.0,  "confirming": true},
      "nasa_firms": {"signals": 4, "contribution": 0.55, "ceiling": 0.55, "confirming": false},
      "citizen":    {"signals": 1, "contribution": 0.4,  "ceiling": 0.75, "confirming": false},
      "senapred":   {"signals": 1, "contribution": 0.85, "ceiling": 0.85, "confirming": false}
    },
    "combined": 1.0,
    "combination": "additive_capped",
    "ceiling_applied": "confirming_source",
    "level": "confirmed",
    "level_label": "Incendio confirmado",
    "thresholds": {"unsafe_below": 0.3, "confirmed_above": 0.6},
    "alert": {"level": "roja", "confidence": 1.0}
  }
}
```

El `confidence_breakdown` viaja en la respuesta a propósito: un número de
confianza sin su derivación no es auditable, y este número puede terminar
moviendo camiones.

`/incidents/correlate` y los endpoints de collectors deben quedar detrás de
autenticación de operador en producción: los primeros abren transacciones largas
sobre la tabla de señales, los segundos lanzan tráfico saliente hacia APIs de
terceros con cuota.

---

## El collector de FIRMS

```
GET {base}/api/area/csv/{MAP_KEY}/{SENSOR}/{west,south,east,north}/{day_range}
```

Sensores por defecto: `VIIRS_SNPP_NRT`, `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`,
`MODIS_NRT`. Bounding box por defecto: `-72.0,-33.8,-69.8,-32.0`.

Tres detalles que no son obvios y están cubiertos por tests:

**FIRMS no entrega un ID propio.** El `external_id` es
`sha256(sensor|satélite|instrumento|fecha|hora|lat|lon)`. Determinista, así que
reejecutar sobre la misma ventana no duplica.

**`acq_time` viene sin ceros a la izquierda.** `"24"` son las 00:24 UTC, no las
24:00. Un `int()` ingenuo desplaza detecciones nocturnas un día entero.

**FIRMS responde HTTP 200 con texto plano cuando la MAP_KEY es inválida o se
agotó la cuota.** El parser lo detecta y falla explícitamente. Tratarlo como
"cero detecciones" haría que el collector fallara en silencio durante días —
el peor modo de falla posible para un sistema cuyo propósito es recolectar.

Confianza: VIIRS es categórica (`l`/`n`/`h` → 0.30/0.55/0.80); MODIS es
porcentual (0–100 → [0.10, 0.85]). El techo es deliberado: FIRMS corrobora, no
confirma.

---

## Los collectors institucionales: CONAF y SENAPRED

Ambos leen servicios geoespaciales públicos a través de `collectors/geoservices.py`
(cliente ArcGIS con paginación, cliente OGC WFS, parseo de GeoJSON y cadena de
respaldos). Ninguno necesita credenciales.

### CONAF — `conaf_incendios`

Capa operativa de incendios del SIT (PostGIS detrás, publicada como
FeatureServer con salida GeoJSON). Campos: `id`, `nombre`, `estado`, `f_inicio`,
`f_control`, `f_extincion`, `sup_total`, `lat`, `lon`, `comuna`, `provincia`,
`region`.

| Decisión | Por qué |
|---|---|
| `type = wildfire`, `confidence = 1.0` | CONAF es el organismo que combate el incendio: su registro **confirma** el hecho. Es exactamente el contrapunto de FIRMS, que sólo sugiere. |
| `external_id = conaf:{id}` | CONAF sí entrega ID propio. Un incendio se relee mientras avanza de *En Combate* a *Controlado* y a *Extinguido*: el upsert actualiza su fila en vez de multiplicarla en el mapa. |
| Ventana de `CONAF_LOOKBACK_DAYS` días | La relectura de la ventana **es** el seguimiento del ciclo de vida, no un desperdicio. |
| Filtro por región en memoria | Comparación sin tildes ni mayúsculas; si la capa no trae el campo región, se cae al bounding box. |

### SENAPRED — `senapred_alertas`

SENAPRED no publica una API documentada de alertas en tiempo real. Se lee la capa
de alertas vigentes que alimenta sus visores institucionales. Campos: `Region`,
`Alerta`, `Razon`, `Evento`, `Comunas`, `Ambito`, `Fecha`, `Actualizado`.

| Decisión | Por qué |
|---|---|
| `type = alert` / `evacuation`, nunca `wildfire` | Una alerta es un **acto administrativo**, no la observación del fenómeno. Promoverla a incendio confirmado inflaría artificialmente la confianza del incidente que arme el correlacionador. La confirmación la aporta CONAF. |
| `confidence = 1.0` | El acto es cierto por definición: SENAPRED lo declaró. |
| `external_id` = hash de región+comunas+nivel+evento+motivo+fecha | La capa no expone folio. Se excluye a propósito `Actualizado`, que cambia en cada refresco: si entrara, cada corrida sembraría un duplicado de la misma alerta. |
| Eventos sin coordenadas | La capa es tabular: la alerta cubre una comuna o una región completa. Inventarle un centroide sería peor que no tenerlo — el correlacionador lo trataría como ubicación real. El esquema ya admite eventos sólo con texto. |
| Alertas nacionales incluidas | `SENAPRED_INCLUDE_NATIONAL=true`: una alerta de ámbito nacional también rige en la V región. |

### Cómo no fallar en silencio

El peor modo de falla de este sistema no es caerse: es reportar `success` con
cero eventos mientras una institución cambió de formato. Cuatro defensas, todas
con test:

1. **Errores con HTTP 200.** ArcGIS y GeoServer devuelven `{"error": …}` con
   código 200. Se detecta y la corrida falla.
2. **HTML en vez de JSON.** Un portal caído responde una página de error. Se
   detecta por el cuerpo, no por el código de estado.
3. **`features` ausente.** Si desapareciera la clave, devolver `[]` equivaldría a
   inventar que no hubo emergencias. Se distingue de una colección legítimamente
   vacía.
4. **Filtro roto.** Si el `WHERE` acotado es rechazado —típicamente porque
   renombraron el campo de fecha— se reintenta sin filtro y la corrida queda en
   `partial` con la advertencia, en vez de devolver cero incendios.

Las degradaciones no fatales (respaldo usado, filas sin fecha, nivel de alerta
irreconocible) se acumulan vía `self.warn(...)`, dejan la corrida en `partial` y
quedan escritas en `collector_runs.error`. El operador las ve sin leer logs.

### Si una institución cambia de plataforma

`CONAF_SOURCES` y `SENAPRED_SOURCES` aceptan una **cadena de respaldos**:

```
kind|url[|layer];kind|url[|layer]        # kind = arcgis | wfs | geojson
```

Se intentan en orden y la primera que responde algo interpretable gana. Si el SIT
de CONAF publica su WFS, se antepone sin tocar código:

```
CONAF_SOURCES=wfs|https://sit.conaf.cl/wfs|conaf:incendios_activos;arcgis|https://…/FeatureServer|0
```

El uso de un respaldo nunca pasa inadvertido: queda como advertencia en la
corrida. Además, los campos se leen por *alias* (`FIELD_ALIASES` en cada
collector), de modo que un renombre cosmético de columna no deja al sistema ciego.

### Calibración de zona horaria

ArcGIS especifica que los campos fecha viajan en epoch UTC y ese es el supuesto
por defecto (`CONAF_TIME_OFFSET_MINUTES=0`). Hay un matiz que conviene verificar
contra un incendio conocido: en la capa de CONAF los campos derivados `dia_nom` y
`rango_hora` coinciden con la lectura **UTC** del epoch, lo que es consistente
tanto con "publican en UTC" como con "publican hora local etiquetada como UTC",
un error frecuente al exportar desde una base en hora local.

No se adivinó: un desfase de 4 horas arruinaría cualquier correlación
espaciotemporal, así que la corrección es una decisión explícita y calibrable.
Si al contrastar con un incendio de hora conocida aparece un desfase constante,
se corrige con `CONAF_TIME_OFFSET_MINUTES=-240` sin desplegar.

---

## El motor de correlación

    señales independientes  ──►  un incidente con confianza agregada

Tres piezas, separadas por su dependencia de la base de datos. Las dos primeras
son **funciones puras**: reciben listas y devuelven resultados, sin sesión, sin
red y sin reloj. La parte difícil de este sistema no es el SQL, es decidir
cuánto vale cada señal; mantenerla pura permite recalibrarla con fixtures reales
en segundos, y por eso los 86 tests del motor corren sin levantar nada.

| Módulo | Qué hace | ¿Toca la base? |
|---|---|---|
| `correlation/confidence.py` | Confidence Engine: confianza, tipo, estado y derivación | No |
| `correlation/communes.py` | Paso B: extracción y coincidencia de comunas | No |
| `correlation/engine.py` | Orquesta los pasos y persiste | Sí, sólo él |

### El descubrimiento que obliga a dos pasos

En la fase anterior quedó establecido que la capa de alertas de SENAPRED es
**tabular**: no trae geometría, y se decidió no inventarle un centroide porque el
correlacionador lo trataría como una ubicación real. Esa decisión fue correcta y
su precio es que el motor no puede ser un solo algoritmo espacial.

**Paso A — geometría.** Agrupa las señales que sí tienen coordenadas (FIRMS,
CONAF, reportes ciudadanos, despachos) y es el único paso que **crea**
incidentes.

**Paso B — texto.** Adosa las alertas sin coordenadas a los incidentes
espaciales de su comuna. No crea incidentes: una alerta que no encuentra ninguno
queda sin vincular, y así debe ser.

### Paso A: agrupación espaciotemporal

Dos primitivas de PostGIS, cada una para lo suyo:

**`ST_ClusterDBSCAN` para agrupar señales sueltas entre sí.** Es la operación
correcta porque **no depende del orden de llegada**. Agrupar de forma incremental
—"cada señal se pega a la primera vecina que encuentre"— produce racimos
distintos según el orden de lectura, y eso es indefendible en algo que decide
dónde hay un incendio. Los puntos se proyectan a **UTM 19S (EPSG:32719)** antes
de agrupar para que `eps` vaya en metros y no en grados.

**`ST_DWithin` sobre `geography` para adherir un racimo a un incidente que ya
existe.** Ahí sí interesa la distancia métrica sobre el elipsoide, y el índice
GiST sigue haciendo el prefiltrado por caja envolvente.

| Decisión | Por qué |
|---|---|
| Ventana de agrupación (4 h) ≠ ventana de adhesión (12 h) | Un incendio de CONAF vive días y sigue recibiendo corroboración mucho después de formarse el primer racimo. Usar una sola ventana obligaría a elegir entre agrupar de más o perder señales tardías. |
| Centroide **ponderado por confianza** | Con un punto de CONAF y seis píxeles de VIIRS repartidos por la ladera, el centro sin ponderar se va cerro arriba y el mapa deja de coincidir con el lugar que reportó el organismo. |
| Una fuente confirmatoria abre incidente **aunque venga sola** | Si CONAF dice que hay un incendio, no hace falta que nadie corrobore. |
| Una señal aislada no confirmatoria puede **esperar** (`CORRELATION_MIN_SIGNALS_FOR_INCIDENT`) | En `1` el mapa muestra todo, con su confianza a la vista; en `2` sólo lo corroborado. La señal no se pierde: sigue siendo una `raw_event` y la pasada siguiente la reevalúa junto a lo que haya llegado. |
| Fusión de incidentes convergentes | Un incendio que avanza produce racimos sucesivos que terminan tocándose. Sobrevive el más antiguo —es el que tiene folio circulando por radio— y el absorbido queda en `merged` apuntando a su sucesor, de modo que un folio ya comunicado sigue resolviendo a algo. |
| `stale` ≠ `extinguished` | Que dejen de llegar detecciones satelitales no significa que el fuego se apagó: significa que no pasó un satélite. Sólo una fuente confirmatoria cierra una emergencia. |

### Paso B: correlación por comuna

Una heurística sobre texto, tratada como tal: el vínculo se marca
`link_method = 'commune_text'` y recibe un `link_confidence` **menor** que el de
una coincidencia geométrica (0.70 exacta, 0.55 por inclusión, frente a 1.0
espacial).

La vigencia de una alerta **no se mide con `timestamp`** —ese es la fecha de
declaración y puede ser de hace días— sino con `updated_at`, que el upsert
refresca cada vez que la capa vuelve a publicarla. Que la fila se siga tocando
*es* su vigencia; cuando SENAPRED la levanta, desaparece de la capa, deja de
refrescarse y sale sola de la consulta.

El Paso B se **reconstruye entero** en cada pasada: se borran sus enlaces y se
recalculan. Una alerta levantada tiene que dejar de teñir el mapa, y reconstruir
es más simple de auditar que caducar enlace por enlace. Los vínculos espaciales,
en cambio, son historia y no se tocan nunca.

Dos reglas que evitan falsos positivos caros:

- **Las alertas regionales y nacionales no se adosan a incidentes concretos**
  (`CORRELATION_ATTACH_REGIONAL_ALERTS=false`). Una alerta temprana preventiva
  nacional por temporada de incendios está vigente todo el verano: pegarla a cada
  incidente teñiría el mapa entero sin decir nada sobre ninguno.
- **La familia del fenómeno tiene que coincidir.** Una alerta amarilla por
  crecida no se une a un incendio que casualmente ocurre en la misma comuna. Si
  la alerta no declara el fenómeno, se une igual pero con la confianza del
  vínculo penalizada y una nota que lo dice.

**Límite conocido y medido.** El Paso B sólo alcanza a incidentes que tienen
comuna, y hoy la comuna se deriva de los campos de CONAF. Un incendio sostenido
únicamente por FIRMS y reportes ciudadanos no tiene comuna y queda fuera de
alcance. El motor lo cuenta en `incidents_without_commune` en cada pasada: es la
métrica que dirá con datos —no con intuición— cuándo hace falta la capa de
polígonos comunales. Adivinar la comuna sería peor que no tenerla: un vínculo
falso con una alerta roja es exactamente el error que este sistema no puede
permitirse.

### Confidence Engine

Las reglas, en el orden en que mandan:

Política **v2.0.0** (recalibrada con datos geoespaciales reales).

| Fuente | Aporte | Techo propio | ¿Confirma? |
|---|---|---|---|
| CONAF, Bomberos | 1.00 | 1.00 | **Sí** |
| SENAPRED | 0.85 | 0.85 | No (pero `alert_confidence = 1.0`) |
| Broadcastify (despacho) | **0.80** | 0.90 | No |
| Municipalidad (despacho) | **0.80** | 0.90 | No |
| Municipalidad (otro) | 0.60–0.80 | 0.90 | No |
| Medios | 0.40–0.60 | 0.75 | No |
| NASA FIRMS | **0.40 fijo** | **0.55** | No |
| Cámaras | 0.35–0.55 | 0.70 | No |
| Ciudadanos | **0.25–0.40** | 0.75 | No |
| Redes sociales | 0.20–0.35 | 0.55 | No |
| Clima, USGS | 0.00 | 0.00 | No |

Y un techo global: **sin fuente confirmatoria, ninguna combinación pasa de 0.95.**

**Tramos de estado.** `confidence_level` sale de dos cortes y de nada más:

| Confianza | `confidence_level` | Etiqueta | Color |
|---|---|---|---|
| < 0.30 | `unsafe` | Baja confianza | `#dc2626` rojo de *advertencia* |
| 0.30 – 0.60 | `possible` | Posible emergencia | `#eab308` amarillo |
| > 0.60 | `confirmed` | Incendio confirmado | `#ea580c` naranja |

`confirmed` es un juicio del motor sobre la evidencia acumulada.
`is_official_confirmed` es un hecho institucional: CONAF o Bomberos fueron al
lugar. **No son lo mismo y la UI no debe colapsarlos**: un racimo de despachos
radiales llega a `confirmed` con `is_official_confirmed = False`.

**Cómo se combinan.** Dentro de una misma fuente las señales son *parcialmente
redundantes* —cuatro píxeles de la misma pasada de VIIRS son casi una sola
observación, y tres vecinos del mismo cerro miran el mismo humo—, así que el
aporte de la señal `k`-ésima se descuenta por `decay^k` y el total se recorta en
el techo de la fuente. Entre fuentes distintas hay independencia, y desde la
v2.0.0 los aportes se **suman**, saturando en 1.0: `min(Σ wᵢ, 1.0)`. Si el
satélite pone 40 % y un vecino pone 25 %, el incidente vale 65 %, y esa cuenta se
puede rehacer a mano leyendo `by_source` en el breakdown.

La v1.0.0 usaba *noisy-OR* (`1 - Π(1 - wᵢ)`), que para ese mismo caso daba 55 %:
más conservador, pero un número que nadie puede reconstruir mentalmente en una
sala de operaciones. El riesgo de sumar —que satura rápido— se contiene con
pesos base bajos, techo por fuente y el techo global de 0.95.

*Por qué FIRMS bajó a 0.40 con techo 0.55.* Es el cambio central de la v2.
FIRMS detecta **anomalías térmicas**, y en la V Región eso incluye las chimeneas
de Ventanas, quemas agrícolas autorizadas y hornos de ladrillo. La banda está
colapsada a un valor fijo a propósito: la confianza que trae el píxel mide la
certeza del algoritmo sobre la anomalía, no la probabilidad de que la anomalía
sea un incendio. Una lectura excelente de una chimenea sigue siendo una
chimenea. Con techo `0.55 < 0.60`, **ningún racimo puramente satelital, por
grande que sea, se rotula "incendio confirmado"**.

**Las tres reglas que valía la pena escribir con cuidado:**

*CONAF confirma.* Es el organismo que combate el incendio; su registro **es** la
confirmación. Una sola señal suya lleva el incidente a 1.0.

*SENAPRED declara la respuesta, no el fenómeno.* Su alerta es cierta al 100 % —el
acto administrativo existe, lo firmó el organismo— y eso vive en
`alert_confidence = 1.0` y en `alert_level`. Sobre el fenómeno aporta
corroboración muy fuerte pero **no saturante**, por dos razones concretas: la
alerta no observa el incendio, y su vínculo con este incidente en particular se
estableció por coincidencia de comuna, que es texto. Marcar
`is_official_confirmed` ahí sería afirmar que hay fuego, con certeza, en un punto
que ninguna fuente miró.

*FIRMS corrobora, no confirma.* Un techo explícito impide que cualquier cantidad
de píxeles satelitales llegue sola a 1.0. Cruzada con otra fuente sí empuja por
encima de su propio techo: para eso existe el motor.

Un racimo puramente satelital o de puro humo reportado se tipifica
`possible_fire`, nunca `wildfire`. Es el mismo criterio que en la fase anterior
llevó a que FIRMS emitiera `thermal_anomaly`, aplicado un nivel más arriba.

### Cómo gatillar el motor

**Recomendación: un worker periódico, cada 60–120 s, como proceso aparte.**

```bash
python -m app.services.correlation.runner --loop --interval 120
```

Las razones, y por qué no las alternativas:

**Por qué no por evento (al ingerir cada señal).** La correlación es
inherentemente una operación *de conjunto*: agrupa un racimo completo, recalcula
un centroide, recombina todas las fuentes. Dispararla por señal significa
reevaluar el mismo racimo N veces por lote de FIRMS y multiplicar la contención
sobre las mismas filas. Peor: rompería la propiedad que hace correcto al Paso A,
que es no depender del orden de llegada.

**Por qué no dentro del request de FastAPI.** Una pasada abre transacciones
largas sobre la tabla de señales. Un ciudadano que abre el mapa no debería
pagarlas, ni un redeploy debería interrumpirlas a mitad. El endpoint
`POST /incidents/correlate` existe para calibrar y operar, no para el camino
caliente.

**Por qué la latencia no es un argumento en contra.** El tiempo hasta que un
incendio aparece en el mapa lo domina el *poll de la fuente* (CONAF cada 300 s,
SENAPRED cada 600 s, FIRMS cada 900 s), no el motor. Un motor cada 120 s no
agrega latencia perceptible; bajarlo a 10 s no adelantaría nada porque no habría
datos nuevos que correlacionar.

**Y correr de más es barato.** El motor es idempotente: una pasada sobre una
ventana ya correlacionada no duplica nada —hay una verificación explícita en
`smoke_test.py`—. El costo de correr de más es CPU; el de correr de menos es que
un incendio real tarde minutos de más en aparecer.

**Híbrido, si más adelante hace falta.** El refinamiento natural es que cada
corrida de collector que inserte señales encole un disparo, con el worker
periódico como red de seguridad. Requiere una cola (o `LISTEN/NOTIFY` de
Postgres, que ya está disponible) y no aporta nada mientras la cadencia del
motor sea menor que la de los collectors, que es el caso.

**Antes de escalar a más de una réplica** no hay nada que hacer: ya está
resuelto. Cada pasada toma un `pg_try_advisory_xact_lock` y, si otra está
corriendo, se omite y lo deja anotado en sus `warnings`. Sin eso, dos pasadas
concurrentes leen `incident_id IS NULL` antes de que la otra escriba y crean dos
incidentes para el mismo incendio. Con una réplica no ocurre nunca; con dos,
ocurre el primer día.

En despliegue, el motor y los collectors son dos procesos hermanos:

```yaml
# docker-compose (esquema)
api:         uvicorn app.main:app
collectors:  python -m app.collectors.runner --loop
correlation: python -m app.services.correlation.runner --loop
```

### Calibración

Todos los parámetros son hipótesis de partida, no constantes físicas:

| Variable | Defecto | Qué mueve |
|---|---|---|
| `CORRELATION_RADIUS_M` | 1500 | Radio de agrupación y de fusión |
| `CORRELATION_WINDOW_HOURS` | 4 | Señales que entran a cada pasada |
| `CORRELATION_MATCH_WINDOW_HOURS` | 12 | Antigüedad máxima para adherirse a un incidente abierto |
| `CORRELATION_MIN_SIGNALS_FOR_INCIDENT` | 1 | Cuán estricto es el mapa |
| `CORRELATION_STALE_HOURS` | 12 | Silencio tras el cual un incidente se marca `stale` |
| `CORRELATION_ALERT_VALIDITY_HOURS` | 24 | Cuánto sigue vigente una alerta que dejó de refrescarse |
| `CORRELATION_ATTACH_REGIONAL_ALERTS` | false | Si las alertas regionales tiñen incidentes |
| `CORRELATION_POLL_INTERVAL_SECONDS` | 120 | Cadencia del worker |

El runner acepta `--radius-m`, `--window-hours` y `--min-signals` para probar una
calibración sin tocar el `.env`. Los pesos del Confidence Engine viven en
`RULES`, dentro de `correlation/confidence.py`, con `POLICY_VERSION` versionado:
cuando cambien, los incidentes viejos seguirán diciendo en su
`confidence_breakdown` con qué reglas se calcularon.

---

### Agregar una fuente nueva

Implementar `fetch()` y `normalize()` sobre `BaseCollector`, registrar en
`registry.py`. La idempotencia, la traza en `collector_runs`, el manejo de
errores y el commit ya están resueltos en `run()`.

`normalize()` debe ser una función pura, sin I/O ni base de datos — así el mapeo
se testea con fixtures reales sin levantar nada.

---

## Estado y siguiente paso

Hecho: esquema PostGIS, estructura FastAPI, contrato Pydantic de ingesta,
collectors de FIRMS, CONAF y SENAPRED funcionando, trazabilidad de corridas,
runner con cadencia por fuente, motor de correlación en dos pasos, Confidence
Engine calibrable y endpoints de incidentes para el mapa.

Siguiente: dejar corriendo collectors y motor 7–14 días y **calibrar con datos
reales**. Las preguntas concretas que ese período debe responder:

1. **¿1500 m es el radio correcto?** Contrastar `distance_m` de los vínculos
   espaciales contra incendios de extensión conocida. Un radio grande de más
   funde incendios vecinos; uno chico de menos parte uno solo en varios.
2. **¿Cuántos incidentes se abren y nunca reciben corroboración?** Si son
   muchos, subir `CORRELATION_MIN_SIGNALS_FOR_INCIDENT` a 2. La consulta es
   directa: incidentes con `source_count = 1` y `sources = {nasa_firms}`.
3. **¿Cuánto vale `incidents_without_commune`?** Es la métrica que decide si hace
   falta la capa de polígonos comunales o si CONAF alcanza.
4. **¿Los pesos del Confidence Engine reflejan lo que pasó?** Con incidentes
   cerrados y confirmados a la mano se puede medir, por primera vez con datos,
   qué confianza tenía el motor antes de que CONAF confirmara.
5. **¿Hay encadenamiento de racimos?** DBSCAN con `minpoints = 1` puede encadenar
   puntos a lo largo de una quebrada. Revisar la extensión de los incidentes con
   más señales.

Pendientes del motor:

* **Geocodificación de alertas SENAPRED por polígono comunal.** Hoy el Paso B
  cruza nombres de comuna; con la capa de polígonos el cruce sería geométrico y
  alcanzaría también a los incidentes que sólo tienen FIRMS. Sigue exigiendo
  decidir antes qué significa "una alerta comunal" para el correlacionador: no
  es lo mismo un punto que un área.
* **Trazar cada pasada en una tabla**, como `collector_runs` hace con los
  collectors. Hoy `CorrelationPass` se registra en el log estructurado; llevarla
  a tabla permitiría ver la evolución del motor sin leer logs.
* **Clustering espaciotemporal real.** Hoy la ventana temporal es el `WHERE` y
  DBSCAN agrupa sólo en el espacio. Con datos suficientes se podrá evaluar si
  vale la pena una distancia combinada espacio-tiempo.

Pendientes de la fase de fuentes:

* **Broadcastify** — requiere autorización; usar como fuente de detección vía
  transcripción, sin retransmitir audio.
* **Calibrar el desfase horario de CONAF** contra un incendio de hora conocida
  (ver "Calibración de zona horaria"). Ahora importa más que antes: un desfase de
  cuatro horas sacaría a los incendios de CONAF de la ventana de agrupación y el
  Paso A dejaría de cruzarlos con FIRMS.
* **Confirmar la procedencia de la capa de alertas** de SENAPRED con el
  organismo y, si aparece un endpoint propio, anteponerlo en `SENAPRED_SOURCES`.
  Hoy es el único punto del pipeline que depende de una publicación de terceros;
  por eso la cadena de respaldos y la advertencia en la corrida.

---

## Notas de escalado

Cuando el volumen lo exija, `raw_events` pasa a tabla particionada por RANGE
sobre `timestamp` con particiones mensuales. Todos los índices actuales son
compatibles; el único cambio es que la PK pasa a `(id, timestamp)`.

`incidents` no necesita particionarse: son órdenes de magnitud menos filas. Lo
que sí crece sin parar son los incidentes cerrados, y por eso los índices que
usan el motor y el mapa (`ix_incidents_open_geom`) son **parciales** sobre
`status IN ('active','controlled')`: se mantienen del tamaño del problema real,
no del histórico.

El motor escala horizontalmente sin cambios gracias al advisory lock, pero sólo
una réplica trabaja a la vez; es un patrón de alta disponibilidad, no de
paralelismo. Si alguna vez hiciera falta paralelizar de verdad, el corte natural
es por provincia: los racimos no cruzan esas distancias.
