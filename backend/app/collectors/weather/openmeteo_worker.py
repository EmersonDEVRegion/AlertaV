"""Collector meteorológico táctico — lluvia, incendio, viento, calor e índice UV.

Qué escribe en la base
----------------------

**Dos clases de fila**, y la distinción es el cambio de forma de la v2.

1. Un evento por comuna **con señal** en la ventana:

       source      = weather
       type        = weather_observation      ← nunca `flood`; ver el __init__ del paquete
       lat / lon   = punto de consulta de la comuna
       timestamp   = inicio de la ventana (hora en curso, UTC)
       confidence  = 0.10                    ← línea base de la fuente
       external_id = openmeteo:<comuna>:<YYYYMMDDTHH>
       raw_data    = { comuna, _collector, _weather: { ambito: "comuna", … } }

2. **Una** fila regional por hora, con el estado consolidado de las 36:

       lat / lon   = centroide de los puntos consultados
       external_id = openmeteo:region:<YYYYMMDDTHH>
       raw_data    = { _collector, _weather: { ambito: "region", … } }

Por qué la fila regional existe
--------------------------------

Porque el widget de la barra superior tiene que decir algo **todos los días del
año**, y las comunas sólo hablan cuando pasa algo.

La alternativa evidente —que el frontend consulte Open-Meteo por su cuenta para
los números del estado silencioso— se descartó por lo mismo que el flag de lluvia
no se calcula en el navegador: partiría la capa en dos fuentes con dos cadencias,
y el día que una fallara el widget mostraría 19 °C tranquilos junto a una alerta
roja calculada con otros datos. Una sola fuente, una sola hora de referencia.

La otra alternativa —emitir las 36 comunas siempre— cuesta 864 filas al día para
decir «no pasó nada» en 34 de ellas. La regional cuesta 24, y dice lo mismo.

La fila regional **no es un punto del mapa** y está excluida de la capa comunal
por el discriminador `ambito`. Ver `WeatherService`.

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

Por qué el filtro de comunas sin señal va en `fetch()` y no en `normalize()`
----------------------------------------------------------------------------

Es una desviación deliberada del reparto habitual —`fetch` trae, `normalize`
mapea— y tiene un motivo mecánico. `BaseCollector.run()` calcula
`rejected = fetched - len(events)` y **cualquier** rechazo deja la corrida en
`partial`. Si las 36 comunas entraran a `normalize()` y salieran 2, los 34
descartes de un día tranquilo dejarían este collector permanentemente en
`partial`, que es el mismo ruido que ya hace ilegible el estado del USGS (ver la
nota sobre `partial` en el diagnóstico de las fuentes de accidentes).

Así, `fetched` cuenta las comunas con algo que decir —una métrica que significa
algo— y las tranquilas quedan contadas en `run_params` y en el log a nivel INFO,
igual que hacen los collectors de cortes con los registros de fuera de región.
`normalize()` sigue siendo puro y sigue siendo lo que se testea con datos armados
a mano; la política, que es lo que de verdad hay que probar, vive completa en
`umbrales.py` y `region.py`.

Qué cuenta como «señal», y por qué cambió
------------------------------------------

En la v1 era `hay_lluvia`, y era correcto mientras el collector midiera una sola
variable. Con seis, «no llovió» dejó de ser sinónimo de «no pasa nada»: una tarde
de febrero en Petorca a 38 °C, con 18 % de humedad, ráfagas de 45 km/h y UV 12
tiene 0,0 mm y es exactamente el estado que esta capa existe para describir. Con
el criterio antiguo no habría generado ni una fila.

Ahora es `hay_senal` = lluvia por encima del piso **o** cualquier umbral táctico
cruzado. El piso de lluvia sigue existiendo y sigue significando lo mismo: 0,2 mm
en 24 h es llovizna de modelo y no merece una fila.

Un cero tiene que ser legible
-----------------------------

"Ninguna comuna con señal" es un estado normal y frecuente acá, a diferencia de
casi todas las demás fuentes del sistema. Para que no se confunda con una fuente
muerta, el collector distingue tres situaciones:

* series con datos y sin señal → sólo la fila regional, `success`, y el detalle
  en el log. **Y ésta es otra razón de ser de la fila regional:** una corrida que
  antes terminaba con cero filas —indistinguible de una fuente caída— ahora deja
  siempre una fila que dice, con hora y con datos, «se consultó y no pasaba nada».
* alguna serie sin dato de precipitación, o una variable táctica ausente en las
  36 comunas → advertencia y `partial`.
* **todas** las series sin dato de precipitación → `CollectorError`. Un payload
  con la estructura correcta y la variable vacía en las 36 comunas no es un
  invierno seco: es un campo renombrado, y reportarlo como éxito con cero eventos
  sería el fallo silencioso que este proyecto persigue en todas las fuentes.
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
from app.collectors.weather.region import (
    EstadoRegional,
    consolidar,
)
from app.collectors.weather.region import (
    describir as describir_region,
)
from app.collectors.weather.umbrales import (
    Pronostico,
    Umbrales,
    describir,
    evaluar,
    piso_horario,
)
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

#: Segmento del `external_id` de la fila regional. Literal y no un slug de
#: `NOMBRE_REGION`: el nombre publicado puede cambiar de redacción, y el día que
#: lo haga no puede partir en dos la serie histórica del agregado.
REGION_SLUG = "region"


def build_external_id(pronostico: Pronostico) -> str:
    """ID estable de la observación: comuna + hora de la ventana.

    Open-Meteo no entrega identificador propio —es un pronóstico, no un registro
    con folio— así que hay que construirlo. La hora va truncada porque es la
    granularidad real del dato: dos consultas a las 14:05 y a las 14:40 hablan de
    la misma hora de pronóstico y tienen que colapsar en la misma fila.
    """
    return f"openmeteo:{slug(pronostico.comuna)}:{pronostico.inicio:%Y%m%dT%H}"


def build_region_external_id(estado: EstadoRegional) -> str:
    """El mismo esquema para el agregado, con `region` en lugar de una comuna."""
    return f"openmeteo:{REGION_SLUG}:{estado.inicio:%Y%m%dT%H}"


class OpenMeteoCollector(BaseCollector):
    """Pronóstico táctico por comuna, con el estado consolidado de la región."""

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
        """Los umbrales completos, agrupados por amenaza.

        Se guardan en `collector_runs.params` en cada corrida, y no es
        redundancia con `settings`: cuando dentro de seis meses alguien mire un
        evento de julio y pregunte por qué no disparó, la respuesta tiene que ser
        el umbral **que estaba vigente esa noche**, no el que hay en el `.env` de
        hoy. Es el mismo motivo por el que la confianza se archiva en la fila.
        """
        reglas = self.umbrales
        return {
            "comunas_consultadas": len(self.client.comunas),
            "modelo": self.client.model,
            "variables": list(HOURLY_VARIABLES),
            "ventanas_horas": {
                "lluvia": reglas.ventana_horas,
                "incendio": reglas.ventana_incendio_horas,
                "calor": reglas.ventana_calor_horas,
                "uv": reglas.ventana_uv_horas,
            },
            "umbrales": {
                "lluvia": {
                    "intensidad_mm_h": reglas.intensidad_mm_h,
                    "intensidad_critica_mm_h": reglas.intensidad_critica_mm_h,
                    "acumulado_3h_mm": reglas.acumulado_3h_mm,
                    "acumulado_3h_critico_mm": reglas.acumulado_3h_critico_mm,
                    "acumulado_24h_mm": reglas.acumulado_24h_mm,
                    "acumulado_24h_critico_mm": reglas.acumulado_24h_critico_mm,
                    "mm_minimo_ingesta": reglas.mm_minimo_ingesta,
                },
                "incendio": {
                    "temp_c": reglas.incendio_temp_c,
                    "humedad": reglas.incendio_humedad,
                    "rafaga_kmh": reglas.incendio_rafaga_kmh,
                    "aviso_temp_c": reglas.incendio_aviso_temp_c,
                    "aviso_humedad": reglas.incendio_aviso_humedad,
                    "aviso_rafaga_kmh": reglas.incendio_aviso_rafaga_kmh,
                },
                "viento": {
                    "rafaga_aviso_kmh": reglas.rafaga_aviso_kmh,
                    "rafaga_critica_kmh": reglas.rafaga_critica_kmh,
                },
                "calor": {
                    "aviso_c": reglas.calor_aviso_c,
                    "critico_c": reglas.calor_critico_c,
                    "noche_tropical_c": reglas.noche_tropical_c,
                },
                "uv": {
                    "aviso": reglas.uv_aviso,
                    "critico": reglas.uv_critico,
                },
            },
        }

    async def fetch(self) -> Sequence[Pronostico | EstadoRegional]:
        """Consulta el pronóstico y devuelve las comunas con señal más la región.

        Ver el encabezado del módulo para por qué el filtro vive acá y no en
        `normalize()`, y para qué cuenta como señal.

        La lista **mezcla dos tipos** a propósito. La alternativa —un segundo
        `after_ingest` que escriba la fila regional por su cuenta— la sacaría del
        recuento de `fetched`/`inserted` y del mismo lote transaccional, así que
        una corrida podría dejar las comunas escritas y el agregado no, con el
        widget mostrando la hora anterior sin que nada apareciera en rojo.
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
        con_senal = [item for item in pronosticos if item.hay_senal]

        registros: list[Pronostico | EstadoRegional] = list(con_senal)

        estado = consolidar(
            pronosticos,
            inicio=piso_horario(
                pronosticos[0].inicio if pronosticos else _ahora_utc()
            ),
            fin=pronosticos[0].fin if pronosticos else _ahora_utc(),
            modelo=self.client.model,
        )
        if estado is not None:
            registros.append(estado)

        # A nivel INFO y sin `warn`: que en 34 comunas no pase nada no es una
        # degradación de la corrida. Misma decisión que los collectors de cortes
        # con los registros de fuera de región.
        logger.info(
            "pronóstico táctico evaluado",
            extra={
                "collector": self.name,
                "comunas": len(pronosticos),
                "con_senal": len(con_senal),
                "con_lluvia": sum(1 for item in pronosticos if item.hay_lluvia),
                "severidad_regional": estado.severidad if estado else None,
                "amenaza_regional": estado.amenaza if estado else None,
                "comunas_en_alerta": list(estado.comunas_en_alerta) if estado else [],
            },
        )
        return registros

    def _evaluar(self, serie: SerieComunal) -> Pronostico:
        return evaluar(
            serie.comuna,
            serie.puntos,
            umbrales=self.umbrales,
            modelo=self.client.model,
        )

    def normalize(
        self, records: Sequence[Pronostico | EstadoRegional]
    ) -> list[EventCreate]:
        """Mapea pronósticos y agregado a eventos del dominio. Función pura.

        Sin filtros: cada registro que llega acá produce un evento. Lo único que
        puede descartar una fila es la validación de `EventCreate`, y eso se
        registra como advertencia.
        """
        eventos: list[EventCreate] = []
        invalidos = 0

        for registro in records:
            try:
                eventos.append(
                    self._a_evento_regional(registro)
                    if isinstance(registro, EstadoRegional)
                    else self._a_evento_comunal(registro)
                )
            except Exception as exc:  # una comuna mala no puede tumbar el lote
                invalidos += 1
                logger.debug(
                    "registro meteorológico descartado en validación",
                    extra={"error": str(exc), "registro": type(registro).__name__},
                )

        if invalidos:
            self.warn(
                f"{invalidos} registros no pasaron la validación de EventCreate; "
                f"se descartaron"
            )
        return eventos

    def _a_evento_comunal(self, pronostico: Pronostico) -> EventCreate:
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
            WEATHER_KEY: pronostico.to_dict(),
        }
        return EventCreate(
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

    def _a_evento_regional(self, estado: EstadoRegional) -> EventCreate:
        """El agregado, como fila.

        **Sin la clave `comuna` al nivel de arriba**, y eso es deliberado: ese
        alias es lo que `communes.extract_commune` lee para atribuir una señal a
        una comuna, y el agregado no pertenece a ninguna. Dejarlo ahí con el
        texto "Región de Valparaíso" invitaría a que algún día apareciera como
        una comuna más en un recuento.
        """
        raw_data: dict[str, Any] = {
            "_collector": self.name,
            "_geometry": {
                "type": "Point",
                "coordinates": [estado.lon, estado.lat],
            },
            WEATHER_KEY: estado.to_dict(),
        }
        return EventCreate(
            timestamp=estado.inicio,
            source=EventSource.WEATHER,
            type=EventType.WEATHER_OBSERVATION,
            lat=estado.lat,
            lon=estado.lon,
            text=describir_region(estado),
            external_id=build_region_external_id(estado),
            confidence=WEATHER_CONFIDENCE,
            raw_data=raw_data,
        )


def _ahora_utc():
    """Respaldo para el caso imposible de una lista de comunas vacía.

    `OpenMeteoClient` ya revienta al construirse sin comunas, así que este camino
    no debería existir; está para que `consolidar` reciba fechas válidas y no un
    `None` que reventaría más adelante y más lejos del origen.
    """
    from datetime import UTC, datetime

    return piso_horario(datetime.now(UTC))
