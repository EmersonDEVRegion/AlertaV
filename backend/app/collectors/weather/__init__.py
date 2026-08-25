"""Capa meteorológica: lluvia pronosticada y riesgo de inundación.

Qué es —y qué NO es— una señal de esta capa
--------------------------------------------

Un pronóstico de lluvia **no es un siniestro**. Es la única capa del sistema que
habla del futuro, y eso la separa de todas las demás: FIRMS informa de un píxel
que ya está caliente, Chilquinta de un corte que ya ocurrió, el CSN de un sismo
que ya se midió. Acá se informa de algo que el modelo dice que va a pasar.

De esa distinción salen las tres decisiones que ordenan el módulo:

1. **`type = weather_observation`, nunca `flood`.** `EventType.FLOOD` sí está en
   `CORRELATABLE_EVENT_TYPES` y mapea a `IncidentType.FLOOD`, que el mapa rotula
   "Inundación". Emitir `flood` desde un pronóstico crearía incidentes de
   inundación —con folio, con confianza, con color— por comunas donde no se ha
   inundado nada todavía. Es exactamente el error que el proyecto ya evita con
   `thermal_anomaly` (una anomalía térmica no es un incendio) y con `smoke` (ver
   `CITIZEN_CATEGORY_TO_TYPE` en `app/schemas/event.py`).

2. **`riesgo_inundacion` es un campo del payload, no un tipo de evento.** El
   flag viaja en `raw_data["_weather"]` junto al resto del pronóstico. Sirve para
   pintar una capa y para leer con otros ojos los avisos de ruta cortada que
   lleguen esa misma tarde; no convierte la señal en emergencia.

3. **Confianza 0.10 y peso 0 en el motor.** Ya estaba decidido antes de que
   existiera este collector: `SOURCE_BASE_CONFIDENCE[WEATHER] = 0.10` y
   `confidence.py` le asigna `SourceRule(0, 0, 0, 0)`. La meteorología no aporta
   evidencia sobre que un hecho haya ocurrido, así que no debe mover la aguja de
   ningún incidente. Que llueva mucho no confirma un accidente.

Para qué sirve entonces
-----------------------

Para el cruce que pidió el producto: superponer la lluvia pronosticada con los
cortes de ruta de Transporte Informa y con los cortes eléctricos. Tres avisos de
vía cortada en Valparaíso un día de 8 mm/h describen una tarde muy distinta de
tres avisos en un día seco, y esa lectura la hace un operador mirando dos capas
—no un motor fundiendo dos puntos—. Es el mismo criterio con que los sismos y los
cortes quedaron fuera de la correlación.

Estructura
----------

* `comunas.py`   — las 36 comunas continentales de la V Región con su punto de
                   consulta. Puro.
* `umbrales.py`  — de una serie horaria a un `Pronostico` con `riesgo_inundacion`.
                   Puro, sin red y sin base de datos: es donde vive la política.
* `openmeteo_client.py` — transporte y parseo del payload de Open-Meteo.
* `openmeteo_worker.py` — el collector propiamente tal.
"""

from app.collectors.weather.openmeteo_worker import OpenMeteoCollector

__all__ = ["OpenMeteoCollector"]
