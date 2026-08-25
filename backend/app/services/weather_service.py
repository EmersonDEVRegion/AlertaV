"""Lectura de la capa meteorológica para el mapa.

Delgado, como el sísmico: no hay reglas que aplicar acá. La política —qué cuenta
como riesgo de inundación— se resolvió una sola vez, en
`app/collectors/weather/umbrales.py`, y quedó escrita en el payload. Este
servicio sólo acota la ventana, arma la foto del presente y la traduce a GeoJSON.

Recalcular el flag en la lectura sería el peor camino posible: habría dos
implementaciones de la misma regla, y el día que se muevan los umbrales el mapa y
la base dirían cosas distintas sobre la misma comuna.

Por qué NO hay un `WeatherRepository`
-------------------------------------

Porque no hace falta. La capa meteorológica no tiene tabla satélite: el payload
entero cabe en `raw_events.raw_data`, así que la consulta es un filtro por fuente,
tipo, ventana y bounding box — exactamente lo que `EventRepository.list_events`
ya hace y ya tiene probado. Añadir un repositorio propio sería duplicar ese SQL
para no reutilizar nada.

El día que esto cambie está identificado: si hiciera falta **filtrar en SQL** por
`riesgo_inundacion` sobre meses de histórico, el camino es una tabla satélite
`weather_details` (o un índice GIN sobre el JSONB), no un predicado JSONB suelto.
Hoy no hace falta porque la capa del mapa es una foto acotada: una fila por
comuna, ~36 como máximo.

La foto del presente
--------------------

`list_current` devuelve **la ventana más reciente de cada comuna**, no el
histórico. Es lo que una capa de mapa necesita y además es lo que hace exacto el
filtro por riesgo: si se filtrara después de un `LIMIT` sobre el histórico, un
límite de 100 podría devolver 3 comunas en riesgo habiendo 20. Sobre una foto de
una fila por comuna no hay nada que paginar.

El `LIMIT` que se le pasa al repositorio es una red de seguridad, no paginación.
Como las filas vienen ordenadas por `timestamp` descendente, recortar sólo puede
quitar filas viejas: la ventana más reciente de cada comuna que haya reportado
lluvia recientemente sobrevive siempre.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.enums import EventSource, EventType
from app.repositories.event_repository import EventRepository
from app.schemas.event import GeoJSONFeature, GeoJSONFeatureCollection
from app.schemas.weather import WeatherForecastRead, WeatherStats


class WeatherService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = EventRepository(session)

    @staticmethod
    def weather_bbox() -> tuple[float, float, float, float]:
        """Recorte geográfico: `region_bbox`, no el ancho del sísmico.

        Acá la diferencia con los sismos es real y va en la otra dirección. Un
        sismo a 200 km se siente en Valparaíso y por eso su recorte es más ancho;
        una lluvia a 200 km no moja Valparaíso. Los puntos de consulta son
        comunas de la región, así que el bbox regional no descarta nada legítimo
        y sí protege de que una comuna mal declarada en el `.env` meta un punto
        de otra parte del país en la capa.
        """
        bbox = settings.region_bbox
        return (bbox.west, bbox.south, bbox.east, bbox.north)

    async def list_current(
        self,
        *,
        hours: int = 3,
        solo_riesgo: bool = False,
        limit: int = 500,
    ) -> list[WeatherForecastRead]:
        """La ventana más reciente de cada comuna con lluvia pronosticada.

        `hours` es holgura, no histórico: el pronóstico se reescribe cada hora,
        así que 3 horas cubren una corrida que llegó tarde o un worker que estuvo
        caído un rato, sin arrastrar la lluvia de anteayer al mapa de hoy.
        """
        rows = await self.repo.list_events(
            since=datetime.now(UTC) - timedelta(hours=hours),
            sources=[EventSource.WEATHER],
            types=[EventType.WEATHER_OBSERVATION],
            bbox=self.weather_bbox(),
            limit=limit,
        )

        # `list_events` ordena por timestamp descendente, así que la primera
        # aparición de cada comuna es su ventana más reciente.
        ultima_por_comuna: dict[str, WeatherForecastRead] = {}
        for row in rows:
            lectura = WeatherForecastRead.from_event(row)
            if lectura is None:
                continue
            ultima_por_comuna.setdefault(lectura.comuna, lectura)

        pronosticos = list(ultima_por_comuna.values())
        if solo_riesgo:
            pronosticos = [item for item in pronosticos if item.riesgo_inundacion]

        # Por acumulado descendente: si algo se recorta aguas abajo, que se
        # recorte lo irrelevante. Mismo criterio que el orden por magnitud de la
        # capa sísmica.
        pronosticos.sort(key=lambda item: item.mm_total, reverse=True)
        return pronosticos

    @staticmethod
    def to_geojson(
        pronosticos: Sequence[WeatherForecastRead],
    ) -> GeoJSONFeatureCollection:
        """FeatureCollection para la capa de lluvia de MapLibre.

        Sólo escalares en `properties`: MapLibre serializa a texto cualquier
        objeto o arreglo anidado, así que `motivos` viaja unido en una sola
        cadena en vez de como lista. Es la misma restricción que ya obligó a
        aplanar el detalle sísmico.

        `riesgo_inundacion` es booleano de verdad —no la cadena "true"— porque
        es el campo sobre el que la capa va a filtrar (`["==",
        ["get", "riesgo_inundacion"], true]`) y una expresión de MapLibre no
        compara tipos distintos.
        """
        features = [
            GeoJSONFeature(
                geometry={"type": "Point", "coordinates": [item.lon, item.lat]},
                properties={
                    "public_id": str(item.public_id),
                    "comuna": item.comuna,
                    "inicio": item.inicio.isoformat(),
                    "fin": item.fin.isoformat(),
                    "ventana_horas": item.ventana_horas,
                    "mm_total": item.mm_total,
                    "mm_hora_max": item.mm_hora_max,
                    "mm_3h_max": item.mm_3h_max,
                    "hora_pico": item.hora_pico.isoformat() if item.hora_pico else None,
                    "probabilidad_max": item.probabilidad_max,
                    "horas_con_lluvia": item.horas_con_lluvia,
                    "riesgo_inundacion": item.riesgo_inundacion,
                    "nivel": item.nivel,
                    "motivos": "; ".join(item.motivos),
                    "modelo": item.modelo,
                    # Los dos recordatorios que esta capa necesita: es futuro y
                    # no es una emergencia declarada.
                    "es_pronostico": True,
                    "is_confirmed_incident": False,
                },
            )
            for item in pronosticos
        ]
        return GeoJSONFeatureCollection(features=features)

    @staticmethod
    def stats(pronosticos: Sequence[WeatherForecastRead]) -> WeatherStats:
        en_riesgo = [item for item in pronosticos if item.riesgo_inundacion]
        inicios = [item.inicio for item in pronosticos]
        return WeatherStats(
            comunas=len(pronosticos),
            en_riesgo=len(en_riesgo),
            mm_total_max=max((item.mm_total for item in pronosticos), default=None),
            mm_hora_max=max((item.mm_hora_max for item in pronosticos), default=None),
            # Ya vienen ordenados por acumulado descendente desde `list_current`.
            comunas_en_riesgo=[item.comuna for item in en_riesgo],
            ventana_inicio=max(inicios) if inicios else None,
            ventana_fin=max((item.fin for item in pronosticos), default=None),
        )
