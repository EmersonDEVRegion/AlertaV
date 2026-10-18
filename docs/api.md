# API

Base: `https://alertav-api.onrender.com/api/v1`. El esquema OpenAPI público está
en `/docs`; las rutas de operación y de calibración no aparecen ahí.

**Acceso.** *Pública*: la usa la PWA. *Oculta*: activa, fuera de `/docs`.
*Operador*: exige `Authorization: Bearer <OPERATOR_TOKEN>`; en producción, sin
token configurado, responde 503 (falla cerrada).

## Mapa y PWA

| Método | Ruta | Acceso | Qué devuelve |
|---|---|---|---|
| GET | `/incidents/active` | pública | Incidentes consolidados para el mapa |
| GET | `/incidents/{code}` | pública | Detalle con todas sus señales y el motivo de cada vínculo |
| GET | `/events/seismic` | pública | Sismos con magnitud y profundidad |
| GET | `/events/seismic/hazard` | pública | Capa de amenaza sísmica del CSN (archivo estático, con `ETag`) |
| GET | `/events/cuarteles` | pública | Cuarteles de Bomberos de la región (archivo estático, con `ETag`) |
| GET | `/events/weather/grid` | pública | Grilla de lluvia pronosticada (mapa de calor) |
| GET | `/events/weather/geojson` | pública | Lluvia pronosticada por comuna |
| GET | `/events/weather/tactical` | pública | Estado meteorológico de la región (widget) |
| GET | `/events/road-closures/geojson` | pública | Cortes de ruta (MOP y MTT) |
| GET | `/events/water-cuts/geojson` | pública | Cortes de agua vigentes de Esval |
| GET | `/events/transito/geojson` | pública | Tránsito vigente del MTT (@TTIValparaiso): tramos sobre la calle, puntos o sin geometría |
| GET | `/feed/vehiculos` | pública | Vehículos robados, recuperados y abandonados (GBV), 48 h |
| GET | `/feed/noticias` | pública | Prensa local, 24 h (fuera del mapa) |
| GET | `/feed/avisos-meteo` | pública | Avisos, alertas y alarmas vigentes de la DMC para la región (404 si la fuente está apagada) |
| GET | `/feed/avisos-meteo/zonas` | pública | GeoJSON de las zonas de pronóstico con avisos vigentes y su severidad mayor (capa «Avisos DMC»; 404 si la fuente está apagada) |
| GET | `/collectors/health` | pública | Frescura de la recolección por familia (la lee el mapa) |
| POST | `/events/citizen-report` | pública, con freno por IP, dispositivo y red | Reporte ciudadano |
| GET | `/share/i/{code}` | pública | HTML con Open Graph que redirige a `/?incidente=` (Vercel reenvía `/i/:code`) |

## Avisos push

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/push/status` | ¿Hay push? Clave pública VAPID, radios por defecto y categorías |
| POST | `/push/subscriptions` | Alta o actualización, idempotente por `endpoint` (ubicación, radios y lugares) |
| POST | `/push/unsubscribe` | Baja: borra la suscripción, sus lugares y su registro de envíos |
| POST | `/push/test` | Aviso de prueba a una suscripción registrada (uno por minuto por IP) |

## Integraciones

| Método | Ruta | Acceso | Qué hace |
|---|---|---|---|
| POST | `/apify/webhook` | secreto de Apify | Encola una corrida terminada del Task de X; los workers la procesan |

## Operación y calibración

| Método | Ruta | Acceso | Qué hace |
|---|---|---|---|
| GET | `/collectors` | operador | Collectors registrados |
| GET | `/collectors/runs` | operador | Historial de corridas, con el `error` completo |
| POST | `/collectors/{name}/run` | operador | Disparo manual de un collector |
| POST | `/collectors/backfill-geocoding` | operador | Reintenta geocodificar señales sin punto |
| POST | `/incidents/correlate` | operador | Pasada manual del motor |
| GET | `/moderacion` | operador | Comentarios ciudadanos por estado de moderación |
| POST | `/moderacion/{public_id}` | operador | Aprobar o rechazar un comentario |
| GET | `/events` | operador | Señales crudas con filtros de tiempo, fuente, tipo, bbox y radio |
| GET | `/events/geojson` | operador | Señales crudas como FeatureCollection |
| GET | `/events/stats` | operador | Resumen de la recolección |
| GET | `/events/{public_id}` | operador | Detalle de una señal |
| GET | `/events/{public_id}/neighbours` | operador | Señales cercanas en espacio y tiempo |
| GET | `/incidents/geojson` | oculta | Incidentes activos como FeatureCollection |
| GET | `/incidents/stats` | oculta | Resumen de la correlación |
| GET | `/health` | pública | Liveness (la usan Render y el keepalive) |
| GET | `/health/ready` | pública | Readiness: base y PostGIS |

Las lecturas crudas de `/events` piden token porque traen los reportes
ciudadanos con su texto sin revisar y su GPS exacto, que la API pública no
muestra.

## Notas

`/events/seismic/stats`, `/events/weather` y `/events/weather/stats`.

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

`/incidents/correlate` y los disparos de collectors piden token porque el primero
abre transacciones largas sobre la tabla de señales y los segundos lanzan
tráfico saliente hacia APIs de terceros con cuota.
