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

#: Discriminador de ámbito dentro del payload. Espejo de `umbrales.AMBITO_*`,
#: por el mismo motivo y con el mismo test que la clave de arriba.
#:
#: Una fila meteorológica describe **una comuna** o **la región entera**. La
#: segunda no es un punto del mapa: es el agregado que alimenta el widget de la
#: barra superior, y si se colara en la capa comunal aparecería como una comuna
#: fantasma en mitad de la región, con su propia mancha de lluvia.
#:
#: Las filas de la v1 no traen el campo. `scope_of()` las lee como comunales,
#: que es lo que eran: el histórico anterior a este cambio sigue sirviéndose sin
#: migración.
WEATHER_SCOPE_KEY = "ambito"
SCOPE_COMUNA = "comuna"
SCOPE_REGION = "region"


def payload_of(event: Any) -> dict[str, Any] | None:
    """El bloque `_weather` de una fila, o `None` si no lo trae."""
    payload = (getattr(event, "raw_data", None) or {}).get(WEATHER_PAYLOAD_KEY)
    return payload if isinstance(payload, dict) else None


def scope_of(event: Any) -> str:
    """`comuna` o `region`. Las filas sin el campo son comunales."""
    payload = payload_of(event) or {}
    return SCOPE_REGION if payload.get(WEATHER_SCOPE_KEY) == SCOPE_REGION else SCOPE_COMUNA


class WeatherTrigger(BaseModel):
    """Una regla que se cumplió, con la cifra que la cumplió.

    Es la unidad de explicación de toda la capa, y por eso viaja tipada en vez
    de como un diccionario suelto: **el widget la renderiza campo por campo**
    —el valor en grande, la unidad al lado, el umbral debajo— y un contrato
    difuso obligaría al frontend a adivinar cuál de las claves es el número que
    va en 32 px.

    Espejo de `umbrales.Disparo`. Que el número culpable viaje ya resuelto desde
    el backend es lo que evita reimplementar la política en el navegador, que es
    lo que este proyecto no hace en ninguna capa.
    """

    model_config = ConfigDict(from_attributes=True)

    amenaza: str = Field(
        ...,
        description=(
            "`lluvia` (anegamiento urbano) | `remocion` (remoción en masa) | "
            "`incendio` (propagación) | `viento` | `calor` | `uv`."
        ),
    )
    severidad: str = Field(..., description="`aviso` | `critica`.")
    metrica: str = Field(
        ...,
        description=(
            "Clave estable de la métrica (`mm_hora_max`, `temp_max_c`, "
            "`uv_max`, `rafaga_max_kmh`, `regla_30_30_30`). Para elegir ícono y "
            "formato, **nunca** para decidir si hay alerta."
        ),
    )
    valor: float = Field(..., description="La cifra que cruzó el umbral.")
    unidad: str = Field(..., description="`mm/h`, `°C`, `km/h`, `UV`…")
    umbral: float = Field(..., description="Contra qué se comparó.")
    texto: str = Field(..., description="Frase legible ya redactada, en español.")
    momento: datetime | None = Field(
        default=None,
        description=(
            "Cuándo ocurre, en UTC. `null` cuando la regla es de acumulado y no "
            "de instante."
        ),
    )


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

    # -- Ambiente y estado táctico (v2) --------------------------------------
    #
    # Todos con `default=None` y no requeridos, y eso no es laxitud: hay filas de
    # la v1 en la base que no traen ninguno de estos campos. Exigirlos haría que
    # `from_event` devolviera `None` para todo el histórico anterior al cambio, y
    # la capa de lluvia se vaciaría durante las tres horas de la ventana de
    # holgura después de cada despliegue.

    temp_actual_c: float | None = Field(
        default=None, description="Temperatura de la hora en curso, en °C."
    )
    temp_max_c: float | None = None
    temp_min_c: float | None = None
    humedad_min: float | None = Field(
        default=None, description="Humedad relativa mínima de la ventana, en %."
    )
    viento_actual_kmh: float | None = None
    viento_max_kmh: float | None = None
    rafaga_max_kmh: float | None = Field(
        default=None,
        description=(
            "Ráfaga máxima, en km/h. **Es la que gobierna las reglas**: lo que "
            "tumba una rama sobre un cable o levanta una pavesa es el pico, no "
            "el promedio."
        ),
    )
    uv_max: float | None = Field(
        default=None, description="Índice UV máximo de la ventana de UV (6 h)."
    )

    severidad: str = Field(
        default="ninguna",
        description=(
            "Estado táctico consolidado: `ninguna` | `aviso` | `critica`. "
            "**No es lo mismo que `riesgo_inundacion`**: una comuna puede estar "
            "en `critica` por índice UV con 0,0 mm de lluvia."
        ),
    )
    amenaza: str | None = Field(
        default=None,
        description=(
            "Amenaza responsable del estado: `lluvia` (anegamiento urbano) | "
            "`remocion` (remoción en masa) | `incendio` | `viento` | `calor` | "
            "`uv`. `null` en calma."
        ),
    )
    disparo_principal: WeatherTrigger | None = Field(
        default=None,
        description=(
            "La regla que manda. Es lo que la interfaz expande — el número "
            "culpable y contra qué se comparó."
        ),
    )
    disparos: list[WeatherTrigger] = Field(
        default_factory=list,
        description="Todas las reglas que se cumplieron, no sólo la principal.",
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
        payload = payload_of(event)
        if payload is None:
            logger.warning(
                "fila meteorológica sin payload; se omite",
                extra={"public_id": str(getattr(event, "public_id", None))},
            )
            return None

        # La fila regional comparte tabla, fuente y tipo con las comunales, y no
        # es una de ellas. Se descarta sin ruido: el `warning` de arriba está
        # para lo que no debería pasar, y esto pasa una vez por hora a propósito.
        if payload.get(WEATHER_SCOPE_KEY) == SCOPE_REGION:
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
                temp_actual_c=payload.get("temp_actual_c"),
                temp_max_c=payload.get("temp_max_c"),
                temp_min_c=payload.get("temp_min_c"),
                humedad_min=payload.get("humedad_min"),
                viento_actual_kmh=payload.get("viento_actual_kmh"),
                viento_max_kmh=payload.get("viento_max_kmh"),
                rafaga_max_kmh=payload.get("rafaga_max_kmh"),
                uv_max=payload.get("uv_max"),
                severidad=payload.get("severidad", "ninguna"),
                amenaza=payload.get("amenaza"),
                disparo_principal=payload.get("disparo_principal"),
                disparos=list(payload.get("disparos") or []),
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


class TacticalWeatherRead(BaseModel):
    """El estado táctico de la región. **Es el contrato del widget.**

    # La regla que ordena este schema

    Un widget de 180 px en la barra superior de un teléfono tiene sitio para un
    número grande y una línea de contexto. Todo lo que hay acá existe para
    responder dos preguntas y ninguna más:

      * **¿Pasa algo?** → `severidad`. Tres valores, y el diseño del widget se
        deriva de ellos: `ninguna` es gris y silencioso, `aviso` es ámbar,
        `critica` es rojo.
      * **¿Qué, exactamente?** → `disparo_principal` + `comuna_origen`. La
        métrica culpable con su cifra, y dónde.

    El resto es el estado silencioso (`temp_c`, `viento_kmh`) y el detalle que
    se despliega si alguien toca el widget.

    # `temp_c` es una MEDIANA y `temp_max_c` es un máximo

    No son dos formas de decir lo mismo y confundirlas rompe el widget. La
    mediana describe **el ambiente de la región ahora** y es lo que se muestra
    cuando no hay nada que alertar; el máximo describe **el peor punto de la
    ventana** y es lo que dispara la alerta. Un día con 38 °C en Petorca y 17 °C
    en Valparaíso tiene mediana ~21 y máximo 38: mostrar el máximo en calma
    mentiría sobre el tiempo que hace donde está la gente, y mostrar la mediana
    en alerta escondería la única cifra que importaba.

    El razonamiento completo, con por qué es mediana y no media, está en
    `app/collectors/weather/region.py`.

    # Sigue siendo un pronóstico

    `es_pronostico` está por lo mismo que en `WeatherForecastRead`: es la única
    familia de capas del sistema que habla del futuro, y ninguna de sus
    severidades es una alerta declarada. Las declara SENAPRED.
    """

    model_config = ConfigDict(from_attributes=True)

    #: `null` cuando no hay ninguna corrida reciente. **No es lo mismo que
    #: `severidad: "ninguna"`**: uno dice "no sabemos" y el otro "todo
    #: tranquilo", y un widget que los confunda pintará de verde una fuente
    #: caída. Ver `WeatherService.tactical`.
    observado_en: datetime | None = Field(
        default=None,
        description="Inicio de la ventana del agregado más reciente, en UTC.",
    )
    inicio: datetime | None = None
    fin: datetime | None = None

    severidad: str = Field(
        default="ninguna", description="`ninguna` | `aviso` | `critica`."
    )
    amenaza: str | None = None
    disparo_principal: WeatherTrigger | None = None
    comuna_origen: str | None = Field(
        default=None, description="Comuna de la que salió el disparo principal."
    )

    # -- Ambiente: el estado silencioso del widget ---------------------------
    temp_c: float | None = Field(
        default=None, description="**Mediana** regional de la hora en curso, en °C."
    )
    viento_kmh: float | None = Field(
        default=None,
        description=(
            "**Mediana** regional del viento medio, en km/h. Medio y no ráfaga: "
            "describe la tarde, no un instante."
        ),
    )

    # -- Extremos: el estado de alerta y el detalle --------------------------
    temp_max_c: float | None = None
    temp_min_c: float | None = None
    humedad_min: float | None = None
    rafaga_max_kmh: float | None = None
    uv_max: float | None = None

    # -- Recuento ------------------------------------------------------------
    comunas: int = Field(default=0, description="Comunas evaluadas en la corrida.")
    con_lluvia: int = 0
    en_aviso: int = 0
    en_critico: int = 0
    comunas_en_alerta: list[str] = Field(
        default_factory=list, description="De la más grave a la menos."
    )

    modelo: str = "desconocido"
    es_pronostico: bool = True

    @classmethod
    def sin_datos(cls) -> TacticalWeatherRead:
        """El estado cuando no hay ninguna corrida reciente.

        Se distingue de la calma por `observado_en: null` y `comunas: 0`. El
        widget lo dibuja apagado, no en verde: una fuente caída y una tarde
        tranquila no pueden verse igual.
        """
        return cls()

    @classmethod
    def from_event(cls, event: Any) -> TacticalWeatherRead | None:
        """Construye el estado desde la fila regional de `raw_events`.

        Devuelve `None` cuando la fila no es regional o su payload no se puede
        leer. Igual que en `WeatherForecastRead`: un cambio de forma tiene que
        ser visible en el log, no un 500 en la cara del widget.
        """
        payload = payload_of(event)
        if payload is None or payload.get(WEATHER_SCOPE_KEY) != SCOPE_REGION:
            return None

        try:
            return cls(
                observado_en=event.timestamp,
                inicio=payload["inicio"],
                fin=payload["fin"],
                severidad=payload["severidad"],
                amenaza=payload.get("amenaza"),
                disparo_principal=payload.get("disparo_principal"),
                comuna_origen=payload.get("comuna_origen"),
                temp_c=payload.get("temp_c"),
                viento_kmh=payload.get("viento_kmh"),
                temp_max_c=payload.get("temp_max_c"),
                temp_min_c=payload.get("temp_min_c"),
                humedad_min=payload.get("humedad_min"),
                rafaga_max_kmh=payload.get("rafaga_max_kmh"),
                uv_max=payload.get("uv_max"),
                comunas=payload.get("comunas", 0),
                con_lluvia=payload.get("con_lluvia", 0),
                en_aviso=payload.get("en_aviso", 0),
                en_critico=payload.get("en_critico", 0),
                comunas_en_alerta=list(payload.get("comunas_en_alerta") or []),
                modelo=payload.get("modelo", "desconocido"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "payload táctico regional ilegible; se omite",
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
