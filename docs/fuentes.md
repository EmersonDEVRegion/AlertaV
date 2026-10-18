# Fuentes

Qué lee AlertaV, cómo lo lee y qué hace para no fallar en silencio. La lista de
collectors registrados está en `backend/app/collectors/registry.py`; su estado,
en `GET /api/v1/collectors/health`.

| Collector | Fuente | Cómo entra | Cada |
|---|---|---|---|
| `nasa_firms_area` | NASA FIRMS (VIIRS y MODIS) | API de área con `MAP_KEY` | 15 min |
| `conaf_incendios` | CONAF, incendios forestales | Servicios ArcGIS públicos | 5 min |
| `senapred_alertas` | SENAPRED, alertas vigentes | Servicios ArcGIS públicos | 10 min |
| `bomberos_apify_webhook` | Centrales de Bomberos en X | Apify raspa y avisa a `POST /apify/webhook` | 30 min |
| `bomberos_apify_canario` | Las mismas cuentas, sin ventana | Task diario de Apify: mide la ceguera | 24 h |
| `transporte_informa_x` | @TTIValparaiso (MTT) | Llega en la entrega del webhook de Apify | 30 min |
| `mop_vialidad` | MOP, Dirección de Vialidad | Servicio ArcGIS público | 60 min |
| `chilquinta_cortes`, `cge_cortes` | Mapas de interrupciones | Archivos públicos (JS y KMZ) | 5 min |
| `esval_cortes_agua` | Esval, cortes programados | Sitio de Esval vía proxy en Chile | 10 min |
| `csn_sismos`, `usgs_sismos` | CSN y USGS | Catálogo HTML y GeoJSON | 5 min |
| `openmeteo_lluvia` | Open-Meteo, por comuna | API gratuita | 30 min |
| `dmc_avisos` | DMC, avisos, alertas y alarmas | Archivo del mapa del Sistema de Alerta Meteorológica | 10 min |
| (grilla de lluvia) | Open-Meteo, grilla de 0,2° | API gratuita, en lotes | 3 h |
| `prensa_local` | Portales regionales | RSS y HTML | 15 min |
| `gbv_vehiculos` | GBV, vehículos con denuncia | Sitio de la ONG | 30 min |

Los despachos de Bomberos se ubican con la imagen de Viper que adjuntan
@CGI_CBV, @CBVM132, @CBQuilpue y @cbquillota (`collectors/viper.py`, mosaicos
de `tile.openstreetmap.org`) y, si no hay o no calza, con el índice de esquinas
de OSM que arma cada mes el workflow `osm-cruces` desde Geofabrik
(`scripts/osm_cruces.py`). Ver `arquitectura.md`, «Geocodificación».
Un despacho que nombra una sola calle se ubica buscando la imagen en las
esquinas de esa calle (`cruces_locales.puntos_de_calle`, hasta
`VIPER_SEMILLAS_POR_CALLE`).

**El MTT, sólo por X.** El portal `valparaiso.transporteinforma.cl` se leyó cada
10 minutos hasta octubre de 2026 y se retiró: en 3 481 corridas no leyó ningún
aviso, y publicaba unos 4 en dos meses y medio. @TTIValparaiso publica a diario.
Sus tuits de pista ocupada (un bus en panne, una marcha, una obra) entran como
`road_closure`; los de reapertura («Habilitado tránsito en …») no crean señal:
cierran los avisos del MTT de las últimas 12 h que nombran la misma vía
(`_mtt_fin`). Un aviso del MTT sale de la capa de cortes al cerrarse o a las 12 h
sin repetirse; los del MOP siguen hasta que el MOP los retira.

**Ceguera sin aviso.** Una fuente que corre bien pero no lee ni una fila en 72 h
(120 h FIRMS) pasa a `degraded` en `/collectors/health`
(`services/collector_health.py`).

**CGE sólo de la región.** CGE publica todo el país; lo que cae fuera de las
comunas de la V Región no se guarda (`INGEST_SOLO_REGION_FUENTES`).

**Áreas de los cortes.** Las tres empresas publican más que un punto, y AlertaV
lo usa (`core/areas.py`): Chilquinta lista los clientes de cada orden en
`segmentos` y se dibuja su mancha (60 m de margen, sobre una grilla de 20 m;
`ST_Buffer` tardaba 18 s en una orden de 1 718 puntos); CGE trae un `<Point>` y
un `<Polygon>` por corte (el punto publicado manda: antes se promediaba con el
polígono, lo que movía el pin y cambiaba el id del corte); Esval trae los
polígonos de sus sectores. Los barrios que toca cada corte salen de OSM
(`collectors/sectores_osm.py`).

**Programados de Chilquinta.** `tipo: "inter"` es una falla; `"dx"`, una
desconexión programada. El archivo la publica días antes y sin hora de inicio:
entra el día en que termina, desde las 06:00 (`PROGRAMADO_DESDE_HORA`), como
«Corte programado». Hasta el 17-10-2026 entraban como cortes en curso apenas
aparecían (66 incidentes en 30 días). La API de `chilquinta.cl`
(`/service-reports/company/006/…`) trae sectores e inicio, pero exige una llave
de su web: no se usa.

**CGE: «Restauración estimada».** Así escribe CGE la reposición; antes no se
leía y todas sus señales quedaban sin hora.

Los cuarteles de Bomberos (`scripts/sig_bomberos.py`) y la amenaza sísmica
(`scripts/fetch_seismic_hazard.py`) son archivos estáticos en
`backend/static/geo/`, regenerados a mano.

**Descartadas:** Broadcastify (licencia, y no se retransmite audio sin permiso),
Waze for Cities (el convenio no se aprobó), Instagram y prensa por X en Apify
(costo y ruido), el lector RSS de Bomberos por RSSHub (X cerró la ruta),
Quinta Prensa (sin valor propio) y Quinta Visión Ahora (Cloudflare bloquea a
Render).

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

## Avisos meteorológicos de la DMC — `dmc_avisos`

La Dirección Meteorológica de Chile publica tres niveles: **Aviso** (severidad
moderada), **Alerta** (fuerte) y **Alarma** (intensa). El mapa «Sistema de
Alerta Meteorológica» de meteochile.gob.cl se alimenta de un JSON estático,
`archivos.meteochile.gob.cl/portaldmc/AAA/datos_AAA.json` (`DMC_AAA_URL`),
con todo lo vigente del país. No está documentado; el formato verificado está
en `collectors/dmc/aaa_parser.py` y la captura real del 16-10-2026, en
`tests/test_dmc_avisos.py`.

- **Qué entra:** lo que toca la región continental: `05` en una lista de
  regiones o `05_*` en una de zonas. `05m` es la Metropolitana y nunca se
  compara por prefijo. Juan Fernández (`jf`) e Isla de Pascua (`ip`) quedan
  fuera por decisión del proyecto.
- **Qué es:** un pronóstico. Emite `weather_warning`, fuera del motor (no crea
  incidentes, no mueve confianzas, no manda push). Tampoco es una alerta de
  SENAPRED; la PWA lo nombra siempre «DMC · Aviso».
- **Vigencia:** la DMC retira el aviso del archivo cuando deja de regir o lo
  reemplaza por una actualización (`A552-1/2026`). Vigente = visto en la última
  lectura (`_dmc.visto_en`), como Esval. `desde` y `hasta` vienen como texto
  («la tarde del lunes 19»): se traducen a tramos anchos (mañana 06–12, tarde
  12–20, noche 20–06 del día siguiente) que sólo sirven de respaldo.
- **Tabla de valores** (km/h, mm, °C): se guarda sólo la sección de Valparaíso,
  como filas; el HTML de la DMC no llega a la PWA.
- **Condiciones de uso:** el `robots.txt` de `archivos.meteochile.gob.cl` dice
  `Disallow: /`. Es un archivo cada diez minutos, con `User-Agent` propio; cada
  aviso cita y enlaza a la DMC, y se le pidió autorización. Ante un reclamo:
  `DMC_AVISOS_ENABLED=false` en Render (el collector sale del registro y
  `/feed/avisos-meteo` responde 404, que la PWA lee como «no disponible»).
- **Desde dónde sale:** directo. Si algún día el host dejara de responder a
  Render, `DMC_PROXY_URL` lo manda por un proxy en Chile (el de
  `infra/proxy-cl/` tendría que permitir ese host).
- **Zonas:** los polígonos de las cuatro zonas de pronóstico de la región
  (Litoral, Cordillera de la Costa, Precordillera, Cordillera) están
  versionados en `backend/static/geo/dmc_zonas_valpo.json`, recortados de
  `coordenadas_zonas_DMC_v3.geojson` con `python -m scripts.dmc_zonas`. Juntas
  cubren la región continental. Si la DMC publica otra versión (`v4`), se
  vuelve a correr el script con la URL nueva.

## Agregar una fuente nueva

Implementar `fetch()` y `normalize()` sobre `BaseCollector`, registrar en
`registry.py`. La idempotencia, la traza en `collector_runs`, el manejo de
errores y el commit ya están resueltos en `run()`.

`normalize()` debe ser una función pura, sin I/O ni base de datos — así el mapeo
se testea con fixtures reales sin levantar nada.
