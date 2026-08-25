"""Contrato de salida de la capa meteorológica.

Un pronóstico **no es un incidente** ni pretende serlo: no pasa por el motor de
correlación, no acumula confianza y no cambia de estado. Por eso este schema no
comparte nada con `IncidentRead`, igual que el sísmico.

Lo que sí tiene que decir con claridad es que habla del **futuro**. Es la única
capa del sistema que lo hace, y ahí está el malentendido que puede costar caro:
`riesgo_inundacion: true` significa "el modelo anuncia lluvia suficiente para que
esto sea un problema", no "hay una inundación". El campo `es_pronostico`, siempre
en `true`, existe para que la capa de presentación no tenga que deducirlo del
nombre de la ruta.

Los nombres van en español, a diferencia del schema sísmico
--------------------------------------------------------

Y es la misma regla, no una excepción: **los nombres son los del almacenamiento.**
El sísmico usa `magnitude` y `depth_km` porque así se llaman las columnas de
`seismic_details`; acá el dato vive en `raw_events.raw_data['_weather']`, cuyas
claves las escribe `weather/umbrales.py` en español —`mm_total`,
`riesgo_inundacion`, `motivos`—. Traducirlas en la frontera de la API obligaría a
mantener un mapeo que no aporta nada y a que el equipo hablara de `flood_risk` en
el frontend y de `riesgo_inundacion` en los tests del collector. Una sola
palabra por concepto, del collector al mapa.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

#: Clave de `raw_data` donde el collector deja el pronóstico. Espejo de
#: `openmeteo_worker.WEATHER_KEY`; se declara acá para que el schema no importe
#: el collector —la API no debe depender de la capa de recolección— y hay un test
#: que comprueba que los dos valores no se separen.
WEATHER_PAYLOAD_KEY = "_weather"


class WeatherForecastRead(BaseModel):
    """El pronóstico de una comuna para la ventana vigente."""

    model_config = ConfigDict(from_attributes=True)

    public_id: UUID
    comuna: str
    lat: float
    lon: float

    #: Inicio de la ventana evaluada, en UTC. Coincide con `raw_events.timestamp`.
    inicio: datetime = Field(
        ..., description="Inicio de la ventana evaluada, hora truncada, en UTC."
    )
    fin: datetime
    ventana_horas: int

    mm_total: float = Field(..., description="Milímetros acumulados en la ventana.")
    mm_hora_max: float = Field(..., description="Intensidad horaria máxima, en mm/h.")
    mm_3h_max: float = Field(
        ..., description="Máximo acumulado en una ventana móvil de 3 horas."
    )
    hora_pico: datetime | None = Field(
        default=None, description="Hora de la intensidad máxima, en UTC."
    )
    probabilidad_max: int | None = Field(
        default=None,
        description=(
            "Probabilidad de precipitación máxima de la ventana, en %. Puede "
            "venir vacía: no todos los modelos la publican. **No participa del "
            "cálculo de `riesgo_inundacion`** — un escenario grave y poco "
            "probable es justamente el que hay que mostrar."
        ),
    )
    horas_con_lluvia: int

    riesgo_inundacion: bool = Field(
        ...,
        description=(
            "El pronóstico cruza al menos uno de los tres umbrales "
            "(intensidad horaria, acumulado en 3 h, acumulado en la ventana). "
            "Es un **riesgo pronosticado**, no una inundación en curso."
        ),
    )
    nivel: str = Field(
        ...,
        description=(
            "Vocabulario de presentación: `seco` | `lluvia` | `riesgo` | "
            "`riesgo_alto`. Para decidir *si hay riesgo* se lee el booleano, no "
            "esto."
        ),
    )
    motivos: list[str] = Field(
        default_factory=list,
        description=(
            "Qué umbral se cruzó y con qué cifra, en texto legible. Vacío cuando "
            "no hay riesgo."
        ),
    )

    modelo: str = Field(..., description="Modelo de Open-Meteo usado (`best_match`).")
    texto: str | None = Field(
        default=None, description="Descripción legible, la misma que `raw_events.text`."
    )

    #: Constante, y a propósito. Es el recordatorio que evita el malentendido de
    #: esta capa; el GeoJSON de señales crudas lleva `is_confirmed_incident` por
    #: la misma razón.
    es_pronostico: bool = True

    @classmethod
    def from_event(cls, event: Any) -> WeatherForecastRead | None:
        """Construye la lectura desde una fila de `raw_events`.

        Devuelve `None` —y lo registra— cuando la fila no trae el payload del
        collector. No debería ocurrir: las únicas filas con `source = weather`
        las escribe `openmeteo_worker`. Pero es un camino de lectura, y tumbar la
        capa completa del mapa por una fila rara sería peor que servir el resto y
        dejar el rastro en el log.
        """
        payload = (event.raw_data or {}).get(WEATHER_PAYLOAD_KEY)
        if not isinstance(payload, dict):
            logger.warning(
                "fila meteorológica sin payload; se omite",
                extra={"public_id": str(getattr(event, "public_id", None))},
            )
            return None

        try:
            return cls(
                public_id=event.public_id,
                comuna=payload["comuna"],
                lat=payload.get("lat", event.lat),
                lon=payload.get("lon", event.lon),
                inicio=payload["inicio"],
                fin=payload["fin"],
                ventana_horas=payload["ventana_horas"],
                mm_total=payload["mm_total"],
                mm_hora_max=payload["mm_hora_max"],
                mm_3h_max=payload["mm_3h_max"],
                hora_pico=payload.get("hora_pico"),
                probabilidad_max=payload.get("probabilidad_max"),
                horas_con_lluvia=payload.get("horas_con_lluvia", 0),
                riesgo_inundacion=payload["riesgo_inundacion"],
                nivel=payload["nivel"],
                motivos=list(payload.get("motivos") or []),
                modelo=payload.get("modelo", "desconocido"),
                texto=event.text,
            )
        except (KeyError, TypeError, ValueError) as exc:
            # Un cambio de forma del payload tiene que ser visible en el log, no
            # un 500 en la cara del mapa.
            logger.warning(
                "payload meteorológico ilegible; se omite",
                extra={
                    "public_id": str(getattr(event, "public_id", None)),
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return None


class WeatherStats(BaseModel):
    """Resumen de la capa vigente. Lo que cabe en una tarjeta de estado."""

    comunas: int = Field(..., description="Comunas con lluvia pronosticada.")
    en_riesgo: int = Field(..., description="Comunas con `riesgo_inundacion`.")
    mm_total_max: float | None = Field(
        default=None, description="El acumulado más alto de la ventana."
    )
    mm_hora_max: float | None = Field(
        default=None, description="La intensidad horaria más alta de la ventana."
    )
    comunas_en_riesgo: list[str] = Field(
        default_factory=list,
        description="Nombres, ordenados por acumulado descendente.",
    )
    ventana_inicio: datetime | None = None
    ventana_fin: datetime | None = None
