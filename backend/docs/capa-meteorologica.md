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
| `GET /api/v1/events/weather/tactical` | `TacticalWeatherRead` | El estado consolidado de la región. Widget de la barra superior. **Añadida en la v2, ver §10.** |

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

---

# v2 — La capa táctica multiamenaza

**Fecha:** 2026-09-02
**Añade:** `GET /api/v1/events/weather/tactical`, cinco variables nuevas y un estado de alerta
consolidado. **No rompe nada de lo anterior:** `riesgo_inundacion`, `nivel` y `motivos` siguen
significando exactamente lo mismo y siguen calculándose sólo con las tres reglas de agua.

## 8. Qué cambió y por qué

La v1 medía una variable y respondía una pregunta: ¿va a llover lo suficiente? Servía para el
invierno y dejaba la mitad del año en blanco — en la V Región, febrero mata por incendio de
interfaz y por golpe de calor, no por anegamiento.

La v2 pide siete variables horarias (`precipitation`, `precipitation_probability`,
`temperature_2m`, `relative_humidity_2m`, `wind_speed_10m`, `wind_gusts_10m`, `uv_index`) en
la misma petición única para las 36 comunas, y responde **seis** preguntas agrupadas en dos
severidades: `aviso` (ámbar) y `critica` (rojo).

### Las seis amenazas y sus ventanas

| Amenaza | Mecanismo | Ventana |
| --- | --- | --- |
| `lluvia` | Anegamiento urbano: el drenaje se satura en una hora | 24 h |
| `remocion` | Remoción en masa: el suelo se satura y el cerro cede | 24 h |
| `incendio` | Propagación: calor + sequedad + viento sobre el combustible | 12 h |
| `viento` | Ráfaga sola: caída de tendido, suspensión del combate aéreo | 12 h |
| `calor` | Golpe de calor. Riesgo directo a la salud | 24 h |
| `uv` | Daño por exposición sostenida | 6 h |

**Las ventanas son distintas a propósito.** Un acumulado de 24 h describe la saturación de un
cerro, que es lenta; un índice UV de 11 describe lo que le pasa a la piel de alguien que está
afuera *ahora*. Anunciar a las 22:00 que mañana a las 14:00 el UV será 11 no es información
táctica: es ruido que apaga el widget para el día en que sí importa.

## 9. Los umbrales, uno por uno

### Lluvia y remoción en masa

| Métrica | Aviso | Crítico | Amenaza |
| --- | --- | --- | --- |
| Intensidad horaria | 5,0 mm/h | 10,0 mm/h | `lluvia` |
| Acumulado móvil 3 h | 15,0 mm | 25,0 mm | `remocion` |
| Acumulado en 24 h | 40,0 mm | 60,0 mm | `remocion` |

La partición por mecanismo no es cosmética: la intensidad horaria describe el **drenaje urbano**
saturándose —agua corriendo por la calle, un problema de una hora— y el acumulado describe el
**suelo** perdiendo capacidad de infiltración, que es lo que hace ceder un talud. Dos amenazas,
dos respuestas, dos textos distintos en pantalla.

### Propagación de incendios — la regla 30-30-30

| Tramo | Temperatura | Humedad | Ráfaga |
| --- | --- | --- | --- |
| Crítico (30-30-30) | ≥ 30 °C | ≤ 30 % | ≥ 30 km/h |
| Aviso (costero) | ≥ 25 °C | ≤ 40 % | ≥ 25 km/h |

**Las tres condiciones se exigen en el MISMO paso horario.** Comparando máximos independientes,
un día cualquiera de Valparaíso cumpliría el 30-30-30: 31 °C a mediodía, 25 % de humedad por la
tarde y una ráfaga de 40 km/h de madrugada, cuando hacía 11 °C y había rocío. Eso no es un
escenario de propagación, es un artefacto de tres máximos que nunca se encontraron.

**El límite del 30-30-30 hay que decirlo, porque es grande.** No es un índice de peligro validado
—no lo respalda un modelo de combustible— y la propia CONAF mostró en un ejercicio en Laguna
Verde, Valparaíso, que con 18 °C, 48 % de humedad y 20 km/h un incendio puede ser igual de
devastador. En la costa de esta región el 30-30-30 casi nunca se cumple y los incendios ocurren
igual. De ahí el tramo de aviso, que es una decisión propia y no un estándar.

### Viento por sí solo

| Métrica | Aviso | Crítico |
| --- | --- | --- |
| Ráfaga máxima | 60 km/h | 80 km/h |

Independiente del 30-30-30 porque el mecanismo es otro: a 60 km/h se suspende el combate aéreo y
empiezan a caer ramas sobre el tendido —la capa de cortes de luz de este mismo sistema— y a
80 km/h el daño estructural es esperable con o sin fuego. Un temporal invernal de 70 km/h y
12 °C no cumple ninguna condición de incendio y sigue importando.

### Calor — y por qué NO se llama «ola de calor»

| Métrica | Aviso | Crítico |
| --- | --- | --- |
| Temperatura máxima | 32 °C | 36 °C |
| Mínima (noche tropical) | — | ≥ 20 °C agrava el aviso a crítico |

La DMC define ola de calor como *tres días consecutivos por encima del percentil 90 diario de la
climatología de esa estación*. Es la definición correcta y este collector **no puede calcularla**:
no tiene la serie 1991-2020 por estación y un pronóstico de 24 h no ve tres días. Implementar algo
y llamarlo «ola de calor» sería mentir sobre el aval, así que el vocabulario del código y de la
interfaz dice `calor` y nunca «ola de calor».

Lo que sí se mide es el riesgo fisiológico, que no es un percentil sino un número absoluto: 32 °C
es donde la DMC empieza a emitir avisos por altas temperaturas para los valles interiores de esta
región, y 36 °C es el techo de los avisos que efectivamente emitió para Valparaíso en 2026.

La noche tropical **no dispara sola: agrava**. La carga epidemiológica del calor no la produce el
pico de las 15:00 sino la ausencia de alivio nocturno.

### Índice UV — los únicos que no son negociables

| Banda OMS | Valor | Severidad |
| --- | --- | --- |
| Muy alto (rojo) | ≥ 8 | `aviso` |
| Extremo (morado) | ≥ 11 | `critica` |

Son las bandas del índice UV global de la OMS/OMM, idénticas en las escalas de la EPA y del
ICNIRP. Moverlas sería inventar una escala nueva con el nombre y los colores de una que la gente
ya reconoce.

## 10. La ruta nueva: `GET /api/v1/events/weather/tactical`

Devuelve un solo objeto con el estado consolidado de las 36 comunas. Es la ruta del widget de la
barra superior, y su forma responde a eso: un número grande y una línea de contexto.

**Tres cosas del contrato que mandan sobre el diseño de la UI:**

1. **`severidad` no es `riesgo_inundacion`.** Una comuna puede estar en `critica` por índice UV
   con 0,0 mm de lluvia.
2. **`temp_c` es una mediana y `temp_max_c` es un máximo.** La mediana describe el ambiente de la
   región ahora y es lo que se muestra en calma; el máximo describe el peor punto y es lo que
   dispara la alerta. Un día con 38 °C en Petorca y 17 °C en Valparaíso tiene mediana ~21:
   mostrar 38 en calma mentiría sobre el tiempo que hace donde está la gente.
3. **`observado_en: null` no es `severidad: "ninguna"`.** Uno dice «no sabemos» y el otro «todo
   tranquilo». Una interfaz que los pinte igual mostrará calma cuando la fuente esté caída.

`disparo_principal` viaja con la política ya resuelta —`{amenaza, severidad, metrica, valor,
unidad, umbral, texto, momento}`— para que el navegador no tenga que reimplementarla sólo para
poder explicarla.

## 11. La fila regional y el discriminador `ambito`

El collector emite ahora **dos clases de fila** sobre la misma tabla, fuente y tipo:

* una por comuna **con señal** (lluvia por encima del piso **o** cualquier umbral cruzado), y
* **una regional por hora**, siempre, con el agregado.

Se distinguen por `_weather.ambito`, que vale `"comuna"` o `"region"`. `WeatherService` filtra por
ahí: sin ese campo, el agregado aparecería como una comuna fantasma en el centro de la V Región,
con su propia mancha de lluvia sobre el mapa. Las filas de la v1 no traen el campo y se leen como
comunales, así que el histórico anterior se sigue sirviendo sin migración.

La fila regional cuesta 24 filas al día y compra dos cosas: que el widget tenga qué decir el 95 %
de los días del año, y que una corrida sin novedades deje rastro en vez de terminar con cero
filas, indistinguible de una fuente muerta.

## 12. Qué cuenta como «señal» y por qué cambió

En la v1 era `hay_lluvia`. Con seis variables, «no llovió» dejó de ser sinónimo de «no pasa nada»:
una tarde de febrero en Petorca a 38 °C, con 18 % de humedad, ráfagas de 45 km/h y UV 12 tiene
0,0 mm y es exactamente el estado que esta capa existe para describir. Con el criterio antiguo no
habría generado ni una fila.

Ahora es `hay_senal` = lluvia por encima del piso **o** cualquier umbral táctico cruzado. El piso
de lluvia (`OPENMETEO_MIN_INGEST_MM`, 0,2 mm) sigue existiendo y sigue significando lo mismo.

## 13. Ninguna regla dispara con un dato ausente

Las cinco variables nuevas son `float | None` de punta a punta, y **ningún umbral se cumple con un
`None`**. Si el modelo no publica humedad relativa, el 30-30-30 no se evalúa en vez de evaluarse
con un cero implícito —que sería una humedad del 0 % y un incendio crítico permanente— o con un
cien implícito, que apagaría la amenaza para siempre sin que nadie se entere.

Una variable ausente **en todas** las comunas sí levanta advertencia y deja la corrida en
`partial`: eso ya no es una noche sin sol, es un campo renombrado. Ausente en algunas, no — sería
el `partial` permanente que ya hace ilegible el estado del USGS.
