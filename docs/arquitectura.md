# Arquitectura

Cómo funciona el backend de AlertaV y por qué está hecho así. Las rutas están en
[`api.md`](api.md), las fuentes en [`fuentes.md`](fuentes.md) y la PWA en
[`pwa.md`](pwa.md).

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

## Procesos

```text
 Fuentes externas ──► collectors (cada 5–60 min) ──┐
 Apify (X) ─────────► POST /apify/webhook (inbox) ──┤
 Vecinos ───────────► POST /events/citizen-report ──┤
                                                    ▼
                                   raw_events (PostgreSQL + PostGIS)
                                                    │
                                motor de correlación (cada 2 min)
                                                    ▼
                                   incidents + incident_events
                                                    │
                     ┌──────────────────────────────┼──────────────────┐
                     ▼                              ▼                  ▼
            API REST /api/v1               notificador push     /share/i/{code}
                     │                       (cada 1 min)        (Open Graph)
                     ▼
         PWA (React + MapLibre) en Vercel
```

Un solo contenedor en Render corre dos procesos supervisados por `start.sh`:

* **API**: `uvicorn app.main:app`. No hace trabajo pesado: lee, valida y encola.
* **Workers**: `python -m app.workers`, que junta en un intérprete la
  recolección (`collectors/runner.py`), el inbox del webhook de Apify, la grilla
  de lluvia, la poda de `collector_runs`, el motor de correlación
  (`services/correlation/runner.py`) y el notificador push
  (`services/push/runner.py`).

Comparten proceso por memoria (el plan gratuito da 512 MB): tres intérpretes
parten de ~168 MB y dos de ~122 MB. `WORKER_MODE=split` los separa para depurar,
y `ENABLE_COLLECTORS`, `ENABLE_CORRELATION` y `ENABLE_PUSH` apagan un motor sin
tocar los demás. Cada motor atrapa sus propias excepciones; `start.sh` reinicia
el proceso con backoff si muere.

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
la decisión de no geocodificar las alertas a la fuerza.

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

## El motor de correlación

    señales independientes  ──►  un incidente con confianza agregada

Tres piezas, separadas por su dependencia de la base de datos. Las dos primeras
son **funciones puras**: reciben listas y devuelven resultados, sin sesión, sin
red y sin reloj. La parte difícil de este sistema no es el SQL, es decidir
cuánto vale cada señal; mantenerla pura permite recalibrarla con fixtures reales
en segundos, y por eso los tests del motor corren sin levantar nada.

| Módulo | Qué hace | ¿Toca la base? |
|---|---|---|
| `correlation/confidence.py` | Confidence Engine: confianza, tipo, estado y derivación | No |
| `correlation/communes.py` | Paso B: extracción y coincidencia de comunas | No |
| `correlation/perfiles.py` | Radio y tiempo por familia; perfil estructural | No |
| `correlation/despachos.py` | ¿Dos despachos son el mismo llamado? | No |
| `correlation/fuentes_fijas.py` | Focos de calor fijos que FIRMS ve todas las noches | No |
| `correlation/engine.py` | Orquesta los pasos y persiste | Sí, sólo él |

### El descubrimiento que obliga a dos pasos

La capa de alertas de SENAPRED es **tabular**: no trae geometría, y no se le
inventa un centroide porque el correlacionador lo trataría como una ubicación
real. El precio es que el motor no puede ser un solo algoritmo espacial.

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

### Dos despachos: decide la dirección, no la distancia

Para el resto del motor una señal es un punto con hora. Para los despachos de
Bomberos eso no alcanza: la central ya dijo con la dirección si es el mismo
llamado. Un refuerzo repite la esquina o al menos una calle; un llamado nuevo
nombra otras. En los 30 días al 17-10-2026, de 23 incidentes con varios
despachos, los refuerzos repetían siempre una calle y los mal fundidos nunca (un
1-1 en Urriola / Álvaro Besa junto a un 2-1 en Serrano / Almirante Goñi cinco
horas después; cuatro llamados distintos de Los Andes en un solo incidente).

`correlation/despachos.py` (puro) decide si dos despachos son el mismo llamado,
siempre dentro de `CORRELATION_DESPACHO_BRECHA_HORAS` (3 h) o de la brecha de
la familia si es más corta (2 h en choques): la regla separa llamados, nunca
junta lo que el perfil ya separaba por tiempo.

| Calles | Distancia máxima | Por qué |
|---|---|---|
| Las dos iguales (misma esquina) | 2 km | La diferencia es error de geocodificación: «RUTA 68 / PASARELA PLACILLA KM 99» repetido a los 7 min cayó a 927 m |
| Una compartida | `CORRELATION_DESPACHO_RADIO_M` (500 m); el doble si el segundo sale en 30 min | En Quilpué el CJ-3 salió a los 22 min a otra esquina de la misma calle, a 870 m. Dos choques en kilómetros distintos de la Ruta 68, a 96 min, son dos choques |
| Ninguna | — | Otro llamado, aunque esté en la manzana del lado |
| Kilómetros distintos de la misma ruta (más de 1 km) | — | Otro llamado aunque caigan en el mismo punto: sin Viper, «RUTA LAS PALMAS / KILOMETRO 4» y «/ KILOMETRO 7» se geocodifican igual |

Se aplica en las tres puertas: al agrupar (`partir_por_llamado` corta el racimo
de DBSCAN por llamado; cada señal de otra fuente va con el despacho más cercano
si está dentro del radio de su perfil), al adherir un racimo a un incidente (si
los dos lados tienen despachos, decide la dirección; si no, la geometría) y al
fundir incidentes. Entre un despacho y una señal de otra fuente sigue mandando
la geometría.

**Incendio estructural ≠ forestal.** Dentro de `fire`, un grupo que sólo es
estructural usa su propio perfil (`PERFIL_ESTRUCTURAL`: 500 m, 3 h) en vez del
forestal (1500 m, 8 h). Si aparece algo forestal, manda el forestal: un 2-3 que
pasa a 1-1 en la misma dirección es el mismo incendio.

### Fuentes de calor fijas

La Fundición Chagres (Catemu) aparece en FIRMS casi todas las noches y abría un
«posible incendio» público cada vez. `correlation/fuentes_fijas.py`:

- **Lista** (`static/geo/fuentes_termicas.json`): focos conocidos con su radio y
  de dónde salió el punto (Chagres, refinería de ENAP en Concón).
- **Recurrencia**: un punto con anomalías `FIRMS_FIJA_DIAS` (3) días distintos de
  los últimos `FIRMS_FIJA_VENTANA_DIAS` (7), a `FIRMS_FIJA_RADIO_M` (400 m).

Un racimo **sólo de FIRMS** cuyas señales caen todas en una fuente fija no abre
incidente. No se borra nada: la anomalía queda como señal y se suma a un
incidente que abra otra fuente (Bomberos, CONAF, un vecino). Un incendio que se
extiende más allá del foco produce píxeles fuera del radio, y esos abren
incidente como siempre. Interruptor: `FIRMS_FUENTES_FIJAS`.

### Geocodificación: de la esquina escrita al punto

Las centrales de Bomberos escriben la esquina («ARLEGUI / TRASLAVIÑA») en el
92 % de los despachos. Nominatim no sabe ubicar cruces: devuelve el punto donde
OSM ancla una de las dos calles, a decenas de metros o a kilómetros de la
esquina. Por eso el punto se resuelve en este orden
(`app/collectors/nominatim.py`, `geocode`):

0. **La imagen de Viper** (`collectors/viper.py`), en las cuatro centrales que
   la adjuntan: su mapa de OSM en zoom 18 trae el marcador del siniestro al
   centro. Se calza la imagen sobre los mosaicos estándar de OSM
   (`collectors/osm_tiles.py`) alrededor de la semilla que dan los pasos
   siguientes, y el centro calzado es el punto (`precision = "despacho"`, a
   pocos metros). Si no calza, Gemini lee la comuna del encabezado por si el
   despacho es de otra comuna. Manda sobre el texto cuando no coinciden.
   Tope diario de mosaicos (`VIPER_MAX_MOSAICOS_DIA`) por la política de uso
   de OSM.
1. **Índice de esquinas** (`collectors/cruces_locales.py`). La tabla
   `osm_cruces` guarda cada nodo de OSM donde se tocan dos vías de nombre
   distinto, con las comunas a menos de ~100 m. La arma cada mes el workflow
   `osm-cruces` desde el extracto de Geofabrik y la carga el propio backend
   desde el release (`services/osm_cruces_service.py`): GitHub nunca ve la base.
   Los nombres se comparan con `core/nombres_via.py`, el mismo módulo que usó
   el script para indexarlos. Sólo se busca con comuna; una esquina única es el
   punto, y con varias (dos calles que se cruzan dos veces) desempata el punto
   de Nominatim.
2. **Numeración** («altura 1400»): Nominatim, aceptado sólo si OSM tiene ese
   número exacto (`precision = "address"`).
3. **Nominatim sobre la calle**, con las guardas de comuna y sector
   (`precision = "street"`), y **el sector solo** si no hay calle.

Overpass queda como respaldo apagado (`OVERPASS_ENABLED`): desde Render fallaba
casi siempre. La PWA dibuja, sólo en el incidente abierto y sin relleno, un
círculo de incertidumbre según la precisión (60 m en un cruce, 200 m sobre una
calle, 600 m en un sector; ninguno con Viper).

### Vínculo por sector

La prensa y las cuentas locales casi nunca dan una esquina, y a veces el
geocodificador ubica la misma casa quemada en dos puntos a kilómetros de
distancia. Lo que sí repiten con las mismas palabras es el sector: "en el sector
de Miraflores Alto".

Los collectors de texto libre (prensa y despachos de X) extraen ese sector
(`app/collectors/lugares.py`) y lo guardan en `raw_data._extraction.sector_clave`
junto con la comuna (`vina del mar|miraflores alto`). El geocodificador lo usa
para descartar una calle que OSM ubica en otro sector y, si no hay calle, para
geocodificar el sector mismo (`precision = "sector"`). El motor lo usa de dos
formas, las dos con `link_method = 'sector_text'`:

- Un racimo con punto que **no tiene incidente a menos de `CORRELATION_RADIUS_M`**
  se une al incidente de su misma familia que nombra el mismo sector, antes de
  abrir uno nuevo.
- Una señal **sin punto** que nombra un sector se une al incidente de su familia
  en ese sector. Como el Paso B, esto no crea incidentes: si no hay nadie ahí,
  la señal espera.

Siempre misma familia, misma comuna y una ventana corta
(`CORRELATION_SECTOR_WINDOW_HOURS`, 3 h): un sector mide un par de kilómetros y
dos incendios en él con medio día de diferencia son dos incendios. El vínculo no
mueve el punto del incidente —`recompute_geometry` sólo promedia los espaciales—
y guarda la distancia para que se vea cuánto discrepaban las fuentes.

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

Tres reglas que evitan falsos positivos caros:

- **Las alertas regionales y nacionales no se adosan a incidentes concretos**
  (`CORRELATION_ATTACH_REGIONAL_ALERTS=false`). Una alerta temprana preventiva
  nacional por temporada de incendios está vigente todo el verano: pegarla a cada
  incidente teñiría el mapa entero sin decir nada sobre ninguno.
- **La familia del fenómeno tiene que coincidir.** Una alerta amarilla por
  crecida no se une a un incendio que casualmente ocurre en la misma comuna. Si
  la alerta no declara el fenómeno, se une igual pero con la confianza del
  vínculo penalizada y una nota que lo dice.
- **Sólo las familias que SENAPRED alerta reciben alertas**
  (`ALERTABLE_FAMILIES`: `fire` e `hydro`). Un corte de luz, un choque o un
  rescate no son el objeto de una alerta, ni siquiera de una que no declara el
  fenómeno: una alerta comunal por viento no convierte cada corte de la comuna
  en «Alerta Amarilla». La ficha de esos incidentes no muestra el eje; en
  incendios e hidro, la barra aparece sólo con una alerta vigente.

**Límite conocido y medido.** El Paso B sólo alcanza a incidentes que tienen
comuna. La comuna sale de los atributos de la fuente, del texto y, como último
recurso, del polígono comunal que contiene el punto (`comunas_region`, migración
0015). El motor cuenta los que igual quedan sin comuna en
`incidents_without_commune` en cada pasada. Adivinar la comuna sería peor que no
tenerla: un vínculo
falso con una alerta roja es exactamente el error que este sistema no puede
permitirse.

### Confidence Engine

Las reglas, en el orden en que mandan:

Política **v2.0.0** (`POLICY_VERSION` y `RULES` en `correlation/confidence.py`).

| Fuente | Aporte | Techo propio | ¿Confirma? |
|---|---|---|---|
| CONAF, Bomberos | 1.00 | 1.00 | **Sí** |
| Chilquinta, CGE (cortes de luz) | 1.00 | 1.00 | **Sí** |
| SENAPRED | 0.85 | 0.85 | No (pero `alert_confidence = 1.0`) |
| Transporte Informa (MTT) | 0.60–0.80 | 0.90 | No |
| Medios | 0.40–0.60 | 0.75 | No |
| NASA FIRMS | **0.40 fijo** | **0.55** | No |
| Ciudadanos | **0.25–0.40** | 0.75 | No |
| Clima, sismos, MOP, GBV, Esval | 0.00 | 0.00 | No |

`RULES` también define pesos para fuentes que hoy no tienen collector
(despachos radiales, municipalidades, cámaras, redes sociales): quedan listos
para cuando existan, sin efecto mientras tanto.

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
el techo de la fuente. Entre fuentes distintas hay independencia, y los aportes
se **suman**, saturando en 1.0: `min(Σ wᵢ, 1.0)`. Si el
satélite pone 40 % y un vecino pone 25 %, el incidente vale 65 %, y esa cuenta se
puede rehacer a mano leyendo `by_source` en el breakdown.

Se descartó *noisy-OR* (`1 - Π(1 - wᵢ)`), que para ese mismo caso da 55 %: más
conservador, pero un número que nadie puede reconstruir mentalmente en una
sala de operaciones. El riesgo de sumar —que satura rápido— se contiene con
pesos base bajos, techo por fuente y el techo global de 0.95.

*Por qué FIRMS vale 0.40 con techo 0.55.* FIRMS detecta **anomalías térmicas**, y en la V Región eso incluye las chimeneas
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
`possible_fire`, nunca `wildfire`. Es el mismo criterio por el que FIRMS emite
`thermal_anomaly`, aplicado un nivel más arriba.

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

En producción el motor corre dentro de `app.workers`, junto a la recolección
(ver «Procesos», arriba).

### Calibración

Todos los parámetros son hipótesis de partida, no constantes físicas:

| Variable | Defecto | Qué mueve |
|---|---|---|
| `CORRELATION_RADIUS_M` | 1500 | Radio de agrupación y de fusión |
| `CORRELATION_WINDOW_HOURS` | 4 | Señales que entran a cada pasada |
| `CORRELATION_MATCH_WINDOW_HOURS` | 12 | Antigüedad máxima para adherirse a un incidente abierto |
| `CORRELATION_SECTOR_WINDOW_HOURS` | 3 | Ventana del vínculo por sector (`sector_text`): misma familia, mismo sector nombrado |
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
