"""Collector meteorológico — lluvia pronosticada y riesgo de inundación.

Qué escribe en la base
----------------------

Un evento por comuna **con lluvia** en las próximas 24 horas:

    source      = weather
    type        = weather_observation      ← nunca `flood`; ver el __init__ del paquete
    lat / lon   = punto de consulta de la comuna
    timestamp   = inicio de la ventana (hora en curso, UTC)
    confidence  = 0.10                    ← línea base de la fuente
    external_id = openmeteo:<comuna>:<YYYYMMDDTHH>
    raw_data    = { comuna, _collector, _weather: {…el pronóstico completo…} }

Idempotencia y por qué la cadencia sale gratis
-----------------------------------------------

La clave del upsert lleva la **hora de la ventana**, no el instante de la
corrida. Dos pasadas dentro de la misma hora escriben la misma fila: la segunda
actualiza `text`, `confidence` y `raw_data` con el último ciclo del modelo y no
inserta nada nuevo (`timestamp` no está en `_UPSERT_UPDATE_COLUMNS`, así que la
fila conserva la hora en que la ventana se abrió). El coste en base de datos de
correr más seguido es cero, y el histórico queda con una fila por comuna y por
hora — que es exactamente la granularidad del pronóstico.

Cadencia
--------

`default_interval_seconds = 1800`, y no los 300 de las fuentes de siniestros. El
razonamiento completo está en `OPENMETEO_POLL_INTERVAL_SECONDS`
(`app/core/config.py`): los modelos globales se recalculan cada 3 a 6 horas, así
que preguntar cada cinco minutos devolvería 35 veces el mismo número.

Por qué el filtro de comunas secas va en `fetch()` y no en `normalize()`
-----------------------------------------------------------------------

Es una desviación deliberada del reparto habitual —`fetch` trae, `normalize`
mapea— y tiene un motivo mecánico. `BaseCollector.run()` calcula
`rejected = fetched - len(events)` y **cualquier** rechazo deja la corrida en
`partial`. Si las 36 comunas entraran a `normalize()` y salieran 2, los 34
descartes de un día de verano dejarían este collector permanentemente en
`partial`, que es el mismo ruido que ya hace ilegible el estado del USGS (ver la
nota sobre `partial` en el diagnóstico de las fuentes de accidentes).

Así, `fetched` cuenta las comunas con lluvia —una métrica que significa algo— y
las secas quedan contadas en `run_params` y en el log a nivel INFO, igual que
hacen los collectors de cortes con los registros de fuera de región. `normalize()`
sigue siendo puro y sigue siendo lo que se testea con datos armados a mano; la
política, que es lo que de verdad hay que probar, vive completa en `umbrales.py`.

Un cero tiene que ser legible
-----------------------------

"Ninguna comuna con lluvia" es un estado normal y frecuente acá, a diferencia de
casi todas las demás fuentes del sistema. Para que no se confunda con una fuente
muerta, el collector distingue tres situaciones:

* series con datos y sin lluvia  → 0 eventos, `success`, y el detalle en el log.
* alguna serie sin dato de precipitación → advertencia y `partial`.
* **todas** las series sin dato → `CollectorError`. Un payload con la estructura
  correcta y la variable vacía en las 36 comunas no es un invierno seco: es un
  campo renombrado, y reportarlo como éxito con cero eventos sería el fallo
  silencioso que este proyecto persigue en todas las fuentes.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from app.collectors.base import BaseCollector
from app.collectors.weather.comunas import slug
from app.collectors.weather.openmeteo_client import (
    HOURLY_VARIABLES,
    OpenMeteoClient,
    SerieComunal,
)
from app.collectors.weather.umbrales import Pronostico, Umbrales, describir, evaluar
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import SOURCE_BASE_CONFIDENCE, EventSource, EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

#: Clave namespaced donde viaja el pronóstico completo dentro de `raw_data`.
#: Mismo patrón que `_seismic` en el USGS y `_outage` en los cortes: el payload
#: útil queda agrupado y un consumidor futuro no tiene que adivinar qué claves
#: del JSON puso el collector y cuáles venían de la fuente.
WEATHER_KEY = "_weather"

#: Confianza de la señal. Sale del catálogo de fuentes (0.10) en vez de un
#: literal para que no queden dos verdades sobre lo mismo. Significa "esto casi
#: no es evidencia de que algo esté ocurriendo", que es exactamente el caso: es
#: un pronóstico. `confidence.py` además le da peso 0 sobre cualquier incidente.
WEATHER_CONFIDENCE = SOURCE_BASE_CONFIDENCE[EventSource.WEATHER]


def build_external_id(pronostico: Pronostico) -> str:
    """ID estable de la observación: comuna + hora de la ventana.

    Open-Meteo no entrega identificador propio —es un pronóstico, no un registro
    con folio— así que hay que construirlo. La hora va truncada porque es la
    granularidad real del dato: dos consultas a las 14:05 y a las 14:40 hablan de
    la misma hora de pronóstico y tienen que colapsar en la misma fila.
    """
    return f"openmeteo:{slug(pronostico.comuna)}:{pronostico.inicio:%Y%m%dT%H}"


class OpenMeteoCollector(BaseCollector):
    """Pronóstico de precipitación por comuna, con flag de riesgo de inundación."""

    name = "openmeteo_lluvia"
    source = EventSource.WEATHER
    default_interval_seconds = 1800

    def __init__(
        self,
        session: Any,
        *,
        client: OpenMeteoClient | None = None,
        umbrales: Umbrales | None = None,
    ) -> None:
        super().__init__(session)
        self.client = client or OpenMeteoClient()
        if umbrales is not None:
            self._umbrales = umbrales

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.OPENMETEO_POLL_INTERVAL_SECONDS

    @property
    def umbrales(self) -> Umbrales:
        """Política de umbrales, construida perezosamente desde `settings`.

        Perezosa por la convención de tests del proyecto: `normalize()` se
        ejercita sobre instancias creadas con `__new__`, sin pasar por
        `__init__`. Un atributo de instancia normal rompería esos tests.
        """
        existing = getattr(self, "_umbrales", None)
        if existing is None:
            existing = Umbrales.from_settings()
            self._umbrales = existing
        return existing

    def run_params(self) -> dict[str, Any]:
        reglas = self.umbrales
        return {
            "comunas_consultadas": len(self.client.comunas),
            "modelo": self.client.model,
            "ventana_horas": reglas.ventana_horas,
            "umbrales": {
                "intensidad_mm_h": reglas.intensidad_mm_h,
                "acumulado_3h_mm": reglas.acumulado_3h_mm,
                "acumulado_24h_mm": reglas.acumulado_24h_mm,
                "mm_minimo_ingesta": reglas.mm_minimo_ingesta,
            },
        }

    async def fetch(self) -> Sequence[Pronostico]:
        """Consulta el pronóstico y devuelve sólo las comunas con lluvia.

        Ver el encabezado del módulo para por qué el filtro vive acá y no en
        `normalize()`.
        """
        series, advertencias = await self.client.fetch_forecast()
        for mensaje in advertencias:
            self.warn(mensaje)

        if series and all(serie.datos_validos == 0 for serie in series):
            raise CollectorError(
                f"ninguna de las {len(series)} comunas trajo datos de precipitación: "
                f"la respuesta tiene la forma correcta y la variable vacía, lo que "
                f"apunta a un cambio en la API y no a un día sin lluvia",
                detail={"variables_pedidas": list(HOURLY_VARIABLES)},
            )

        pronosticos = [self._evaluar(serie) for serie in series]
        con_lluvia = [pronostico for pronostico in pronosticos if pronostico.hay_lluvia]
        en_riesgo = [p for p in con_lluvia if p.riesgo_inundacion]

        # A nivel INFO y sin `warn`: que en 34 comunas no llueva no es una
        # degradación de la corrida. Misma decisión que los collectors de cortes
        # con los registros de fuera de región.
        logger.info(
            "pronóstico evaluado",
            extra={
                "collector": self.name,
                "comunas": len(pronosticos),
                "con_lluvia": len(con_lluvia),
                "en_riesgo": len(en_riesgo),
                "comunas_en_riesgo": [p.comuna for p in en_riesgo],
            },
        )
        return con_lluvia

    def _evaluar(self, serie: SerieComunal) -> Pronostico:
        return evaluar(
            serie.comuna,
            serie.puntos,
            umbrales=self.umbrales,
            modelo=self.client.model,
        )

    def normalize(self, records: Sequence[Pronostico]) -> list[EventCreate]:
        """Mapea pronósticos a eventos del dominio. Función pura.

        Sin filtros: cada pronóstico que llega acá produce un evento. Lo único
        que puede descartar una fila es la validación de `EventCreate`, y eso se
        registra como advertencia.
        """
        eventos: list[EventCreate] = []
        invalidos = 0

        for pronostico in records:
            payload = pronostico.to_dict()
            raw_data: dict[str, Any] = {
                # Al nivel de arriba y con este nombre exacto porque es el alias
                # que `communes.extract_commune` sabe leer (`COMMUNE_ALIASES`).
                # Hoy no se usa —la meteorología no se correlaciona— pero deja la
                # fila legible por la misma maquinaria que el resto.
                "comuna": pronostico.comuna,
                "_collector": self.name,
                "_geometry": {
                    "type": "Point",
                    "coordinates": [pronostico.lon, pronostico.lat],
                },
                WEATHER_KEY: payload,
            }

            try:
                eventos.append(
                    EventCreate(
                        timestamp=pronostico.inicio,
                        source=EventSource.WEATHER,
                        type=EventType.WEATHER_OBSERVATION,
                        lat=pronostico.lat,
                        lon=pronostico.lon,
                        text=describir(pronostico),
                        external_id=build_external_id(pronostico),
                        confidence=WEATHER_CONFIDENCE,
                        raw_data=raw_data,
                    )
                )
            except Exception as exc:  # una comuna mala no puede tumbar el lote
                invalidos += 1
                logger.debug(
                    "pronóstico descartado en validación",
                    extra={"error": str(exc), "comuna": pronostico.comuna},
                )

        if invalidos:
            self.warn(
                f"{invalidos} pronósticos no pasaron la validación de EventCreate; "
                f"se descartaron"
            )
        return eventos
