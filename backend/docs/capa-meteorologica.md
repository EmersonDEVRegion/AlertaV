# Capa meteorológica — contrato para el frontend

**Fecha:** 2026-08-25
**Backend:** `app/collectors/weather/`, `app/services/weather_service.py`, `app/schemas/weather.py`
**Rutas:** `GET /api/v1/events/weather`, `/weather/geojson`, `/weather/stats`

Este documento es el hand-off. Todo lo que hace falta para construir la capa de lluvia y
superponerla a los cortes de ruta está acá; el backend ya está desplegable y con tests.

---

## 1. Qué es esta capa, y qué no es

Es la **única capa del sistema que habla del futuro**. Todas las demás informan de algo que ya
ocurrió: un píxel caliente, un corte registrado por la distribuidora, un sismo medido. Esta
informa de lo que un modelo meteorológico anuncia para las próximas 24 horas.

De ahí salen las tres reglas de presentación que no se pueden romper:

1. **`riesgo_inundacion: true` no es una inundación.** Significa que el pronóstico cruza al
   menos uno de tres umbrales. La UI tiene que decir "riesgo pronosticado", nunca "inundación".
   Cada feature lleva `es_pronostico: true` para que eso no dependa de recordar de qué ruta vino.
2. **No es una alerta oficial.** Las alertas las declara SENAPRED y llegan por otra vía
   (`alert_level` en los incidentes). El campo `texto` ya trae esa aclaración redactada.
3. **Es de escala comunal.** El modelo global tiene celdas de 9 a 11 km: Valparaíso, Viña y
   Concón pueden compartir celda. No sirve para decir "esta quebrada", sirve para decir "esta
   comuna". Un punto por comuna es la representación honesta; un heatmap interpolado insinuaría
   una resolución que el dato no tiene.

El motor de correlación **no** ve esta capa: el evento es `weather_observation`, que está fuera
de `CORRELATABLE_EVENT_TYPES` y pesa 0 en el cálculo de confianza. No crea incidentes y no mueve
la confianza de ninguno. Existe para superponerse.

---

## 2. Las rutas

| Ruta | Devuelve | Para qué |
| --- | --- | --- |
| `GET /api/v1/events/weather` | `WeatherForecastRead[]` | Lista tipada. Fichas, tarjetas, tablas. |
| `GET /api/v1/events/weather/geojson` | `FeatureCollection` | La capa de MapLibre. |
| `GET /api/v1/events/weather/stats` | `WeatherStats` | Tarjeta de estado / badge. |

Parámetros comunes:

| Parámetro | Rango | Defecto | Qué hace |
| --- | --- | --- | --- |
| `hours` | 1–48 | `3` | Holgura hacia atrás, **no histórico**. Ver §4. |
| `solo_riesgo` | bool | `false` | Sólo las comunas con el flag. |
| `limit` | 1–2000 | `500` | Red de seguridad, no paginación. |

`stats` sólo acepta `hours`: nunca filtra por riesgo, porque su trabajo es contar las dos cosas.

---

## 3. Payloads reales

Generados con el código de este commit, no escritos a mano.

### `GET /api/v1/events/weather`

```json
[
  {
    "public_id": "3f2b6c1e-0000-4000-8000-000000000001",
    "comuna": "Valparaíso",
    "lat": -33.0472,
    "lon": -71.6127,
    "inicio": "2026-06-15T14:00:00Z",
    "fin": "2026-06-16T14:00:00Z",
    "ventana_horas": 24,
    "mm_total": 23.1,
    "mm_hora_max": 8.2,
    "mm_3h_max": 18.6,
    "hora_pico": "2026-06-15T16:00:00Z",
    "probabilidad_max": 90,
    "horas_con_lluvia": 6,
    "riesgo_inundacion": true,
    "nivel": "riesgo_alto",
    "motivos": [
      "intensidad 8.2 mm/h ≥ 5.0 mm/h",
      "acumulado en 3 h 18.6 mm ≥ 15.0 mm"
    ],
    "modelo": "best_match",
    "texto": "Lluvia pronosticada en Valparaíso: 23.1 mm en 24 h, máximo de 8.2 mm/h hacia las 12:00 h (probabilidad 90 %). RIESGO DE INUNDACIÓN: intensidad 8.2 mm/h ≥ 5.0 mm/h; acumulado en 3 h 18.6 mm ≥ 15.0 mm. Comuna: Valparaíso. Pronóstico de Open-Meteo (best_match); no es una alerta oficial: las declara SENAPRED.",
    "es_pronostico": true
  }
]
```

### `GET /api/v1/events/weather/geojson`

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": { "type": "Point", "coordinates": [-71.6127, -33.0472] },
      "properties": {
        "public_id": "3f2b6c1e-0000-4000-8000-000000000001",
        "comuna": "Valparaíso",
        "inicio": "2026-06-15T14:00:00+00:00",
        "fin": "2026-06-16T14:00:00+00:00",
        "ventana_horas": 24,
        "mm_total": 23.1,
        "mm_hora_max": 8.2,
        "mm_3h_max": 18.6,
        "hora_pico": "2026-06-15T16:00:00+00:00",
        "probabilidad_max": 90,
        "horas_con_lluvia": 6,
        "riesgo_inundacion": true,
        "nivel": "riesgo_alto",
        "motivos": "intensidad 8.2 mm/h ≥ 5.0 mm/h; acumulado en 3 h 18.6 mm ≥ 15.0 mm",
        "modelo": "best_match",
        "es_pronostico": true,
        "is_confirmed_incident": false
      }
    }
  ]
}
```

### `GET /api/v1/events/weather/stats`

```json
{
  "comunas": 2,
  "en_riesgo": 1,
  "mm_total_max": 23.1,
  "mm_hora_max": 8.2,
  "comunas_en_riesgo": ["Valparaíso"],
  "ventana_inicio": "2026-06-15T14:00:00Z",
  "ventana_fin": "2026-06-16T14:00:00Z"
}
```

### Diferencias entre los dos formatos, a propósito

* **`motivos`**: lista en el JSON tipado, **cadena unida por `; `** en el GeoJSON. MapLibre
  serializa a texto cualquier arreglo u objeto anidado en `properties`, así que una lista del
  otro lado llegaría como `"[\"intensidad…\"]"`. Sin riesgo: `""` cuando no hay riesgo.
* **Fechas**: el JSON tipado usa `…Z` (serializador de Pydantic) y el GeoJSON `…+00:00`
  (`isoformat()`). Las dos son ISO-8601 válidas y `new Date()` parsea ambas. La capa sísmica ya
  tenía esa asimetría; se mantiene por consistencia con ella y no por descuido.
* **`nivel`** es vocabulario de presentación (`seco` | `lluvia` | `riesgo` | `riesgo_alto`).
  Sirve para el color. **Para decidir si hay riesgo se lee el booleano**, no la cadena.

---

## 4. Semántica que hay que respetar

**Es una foto, no un histórico.** Cada llamada devuelve **una fila por comuna**: la ventana más
reciente de cada una. `hours=3` es holgura para cubrir una corrida que llegó tarde o un worker
que estuvo caído un rato, no una ventana de consulta. Subirlo a 48 no da 48 horas de series; da
la misma foto con más tolerancia a huecos.

**Una comuna ausente es una comuna seca.** El collector no emite evento por debajo de 0.2 mm en
24 h: 36 comunas × 48 corridas al día de filas diciendo "no llovió" serían ruido puro. La
consecuencia para la UI: **la ausencia es un dato**, no un error de carga. En verano la capa
vuelve vacía durante semanas y eso es correcto — el estado vacío tiene que decir "sin lluvia
pronosticada", nunca "sin datos".

**`probabilidad_max` no filtra nada en el backend, y no debería filtrar en el frontend.** Un
escenario de 20 mm/h con 30 % de probabilidad es justamente el que una app de emergencias tiene
que mostrar. Sirve para modular opacidad o para redactar la ficha, no para esconder el punto.
Puede venir `null`: no todos los modelos publican la variable.

**Cadencia.** El collector corre cada 30 minutos (`OPENMETEO_POLL_INTERVAL_SECONDS`) porque los
modelos globales se recalculan cada 3 a 6 horas. Refrescar en el cliente cada 5 minutos no
aporta nada: `staleTime` de 10 minutos es lo razonable, alineado con lo que ya hace
`useCurrentWind`.

---

## 5. Ojo con lo que ya existe en el frontend

`frontend/src/api/weather.ts` **ya llama a Open-Meteo directamente desde el navegador**, para el
viento del cono de propagación de un incendio (`current_weather`, una llamada por incidente
seleccionado). Eso se queda como está: es otro dato, otro propósito y otra cadencia.

La capa de lluvia **no** debe seguir ese camino. Tiene que pasar por el backend, por tres
razones concretas:

* el flag lo calcula el backend con umbrales configurables por `.env`; recalcularlo en el
  cliente crearía dos implementaciones de la misma regla y el día que se muevan los umbrales el
  mapa y la base dirían cosas distintas;
* son 36 comunas: una llamada por usuario contra el backend, en vez de una llamada por usuario
  contra un servicio gratuito de un tercero;
* el histórico queda en `raw_events` y es lo que permitirá calibrar los umbrales contra los
  avisos de vía cortada del invierno.

Por el nombre ya ocupado, conviene que el cliente nuevo sea un archivo aparte —`api/rain.ts` o
`api/weatherLayer.ts`— y que su docstring diga en una línea la diferencia con `weather.ts`.

---

## 6. Lo que se pidió: superponer lluvia y cortes de ruta

El cruce que motiva la capa: tres avisos de vía cortada en Valparaíso una tarde de 8 mm/h
describen una situación muy distinta de tres avisos en un día seco. El backend deliberadamente
**no** funde esas dos cosas —la meteorología no correlaciona— porque esa lectura la hace una
persona mirando dos capas.

Los accidentes y cortes de ruta llegan por `GET /api/v1/incidents/active` (familia `traffic`).
La superposición es de presentación:

```js
map.addSource('lluvia', { type: 'geojson', data: featureCollection })

// Sólo las comunas en riesgo. `riesgo_inundacion` viaja como booleano real:
// una expresión de MapLibre no compara tipos distintos, así que esto sólo
// funciona porque el backend no lo manda como la cadena "true".
map.addLayer({
  id: 'lluvia-riesgo',
  type: 'circle',
  source: 'lluvia',
  filter: ['==', ['get', 'riesgo_inundacion'], true],
  paint: {
    // El radio comunica intensidad, no certeza.
    'circle-radius': [
      'interpolate', ['linear'], ['zoom'],
      8, ['interpolate', ['linear'], ['get', 'mm_hora_max'], 5, 6, 20, 14],
      13, ['interpolate', ['linear'], ['get', 'mm_hora_max'], 5, 18, 20, 44]
    ],
    'circle-opacity': 0.35,
    'circle-color': [
      'match', ['get', 'nivel'],
      'riesgo_alto', '#1d4ed8',
      '#3b82f6'
    ]
  }
}, 'incidentes')   // debajo de los incidentes: es contexto, no el sujeto del mapa
```

Tres advertencias de implementación:

1. **El orden importa.** La capa de lluvia va **por debajo** de la de incidentes. Es contexto:
   si tapa los puntos de emergencia, invierte la jerarquía del mapa.
2. **El zoom tiene que ser la raíz de la interpolación del radio**, no una capa sobre un radio
   ya interpolado — es el mismo problema que ya se resolvió en las capas derivadas del mapa.
3. **No usar el mismo lenguaje visual que las emergencias.** El azul translúcido y el radio
   grande dicen "condición ambiental"; un pin con ícono diría "hay un evento acá", que es
   exactamente lo que no hay.

---

## 7. Los umbrales son calibrables, no oficiales

| Regla | Defecto | Variable |
| --- | --- | --- |
| Intensidad horaria | 5 mm/h | `OPENMETEO_INTENSITY_MM_H` |
| Acumulado móvil 3 h | 15 mm | `OPENMETEO_ACCUM_3H_MM` |
| Acumulado en la ventana | 40 mm | `OPENMETEO_ACCUM_24H_MM` |

Cualquiera de las tres levanta el flag (OR). **No salen de una norma de la DMC ni de SENAPRED**:
son una hipótesis elegida por la geografía del caso —cerros con pendiente fuerte, quebradas
canalizadas, drenaje urbano antiguo— y se mueven por `.env` sin desplegar. La calibración real
es contrastar esta capa con los avisos de vía cortada de Transporte Informa a lo largo de un
invierno. Conviene que la UI no prometa más precisión que eso.
