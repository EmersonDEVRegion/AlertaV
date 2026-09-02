"""De 36 pronósticos comunales a un estado regional. Módulo puro.

Por qué existe este módulo
--------------------------

El widget de la barra superior tiene sitio para **un** número grande y **una**
línea de contexto. No puede mostrar 36 comunas, y no puede quedarse callado
mientras una de ellas está a 38 °C. Alguien tiene que decidir cuál de las 36
habla por la región, y esa decisión es política —no presentación— así que vive
en el backend y no en el navegador. Es el mismo criterio por el que
`riesgo_inundacion` se calcula acá: dos implementaciones de la misma regla
terminan diciendo cosas distintas el día que una se mueva.

La regla: el peor caso manda, la mediana acompaña
--------------------------------------------------

Y son dos reglas distintas porque responden a dos preguntas distintas.

* **En alerta, el peor caso.** Si Petorca cruza el umbral de calor crítico, el
  widget dice Petorca. Promediar sería exactamente el error: la media regional de
  un día con 38 °C en el interior y 17 °C en la costa da 26 °C, un número que no
  describe a nadie y que apagaría la alerta que sí existe. En una app de
  emergencias, agregar por promedio es una forma de esconder el problema.

* **En calma, la mediana.** Cuando no hay nada que alertar, el widget está
  describiendo el ambiente, y ahí el peor caso sería igual de engañoso al revés:
  «31 °C» porque una comuna interior está a 31 mientras Valparaíso está a 16. La
  **mediana** —no la media— porque la distribución es bimodal por geografía: 36
  comunas repartidas entre litoral y valles interiores, y la media cae en el
  hueco entre los dos modos. La mediana cae en uno de ellos, que al menos es un
  sitio donde vive gente.

Las comunas de cordillera no se excluyen
-----------------------------------------

Se evaluó y se descartó. Los Andes, San Esteban y Putaendo desvían la mediana
hacia arriba en verano y hacia abajo en invierno, y la tentación de sacarlas del
promedio es real. Pero son 36 comunas con población, no estaciones de referencia:
excluir una del ambiente regional y no de la alerta produciría el estado
incoherente de un widget que dice «22 °C, en calma» mientras su propia lista de
comunas en alerta nombra a Putaendo. Un solo conjunto para las dos preguntas.

Qué NO hace este módulo
------------------------

No decide umbrales —eso es `umbrales.py`— y no lee la base de datos. Recibe
`Pronostico` ya evaluados y devuelve un agregado. Es puro para que el reparto
regional se pueda testear con tres comunas escritas a mano.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Any

from app.collectors.weather.umbrales import (
    AMBITO_KEY,
    AMBITO_REGION,
    ORDEN_SEVERIDAD,
    SEVERIDAD_AVISO,
    SEVERIDAD_CRITICA,
    SEVERIDAD_NINGUNA,
    Disparo,
    Pronostico,
    mas_grave,
)

# `AMBITO_KEY` y `AMBITO_REGION` viven en `umbrales.py` y no acá: `Pronostico`
# también tiene que marcarse, y si las constantes estuvieran en este módulo el
# import sería circular. Ver la nota en el sitio donde se declaran.
#
# Su razón de ser es esta: la capa del mapa y el widget consumen la misma tabla.
# Sin el discriminador, el punto regional aparecería como una comuna fantasma en
# el centro de la V Región, con su propia mancha de lluvia. `WeatherService`
# filtra por acá.

#: Nombre con que se publica el agregado. No es una comuna y no debe poder
#: confundirse con una: `communes.extract_commune` no lo reconoce, que es
#: justamente lo que se quiere.
NOMBRE_REGION = "Región de Valparaíso"


@dataclass(frozen=True, slots=True)
class EstadoRegional:
    """El estado táctico de la región. Es lo que alimenta el widget.

    Un solo objeto por corrida, y por eso se puede permitir llevar tanto: el
    coste de serializarlo es una fila cada hora.
    """

    inicio: datetime
    fin: datetime
    lat: float
    lon: float

    #: La peor severidad de la región. `ninguna` es el estado más frecuente del
    #: año y no es un error de carga.
    severidad: str
    #: Amenaza responsable, o `None` en calma.
    amenaza: str | None
    #: El disparo que el widget expande, con su cifra y su umbral.
    disparo: Disparo | None
    #: Comuna de la que salió ese disparo. `None` en calma.
    comuna_origen: str | None

    # -- Ambiente (estado silencioso del widget) -----------------------------
    #: Medianas regionales de la hora en curso. `None` cuando el modelo no
    #: publicó la variable en ninguna comuna.
    temp_c: float | None
    viento_kmh: float | None
    #: Extremos regionales. El widget los usa en el estado de alerta y en el
    #: detalle expandido, nunca en el silencioso.
    temp_max_c: float | None
    temp_min_c: float | None
    humedad_min: float | None
    rafaga_max_kmh: float | None
    uv_max: float | None

    # -- Recuento ------------------------------------------------------------
    comunas: int
    con_lluvia: int
    en_aviso: int
    en_critico: int
    #: Nombres, de la más grave a la menos. Cabe en una lista desplegable.
    comunas_en_alerta: tuple[str, ...]

    modelo: str

    def to_dict(self) -> dict[str, Any]:
        """JSON plano para `raw_data['_weather']`. Ver la nota de `Pronostico.to_dict`."""
        return {
            AMBITO_KEY: AMBITO_REGION,
            "comuna": NOMBRE_REGION,
            "lat": self.lat,
            "lon": self.lon,
            "inicio": self.inicio.isoformat(),
            "fin": self.fin.isoformat(),
            "severidad": self.severidad,
            "amenaza": self.amenaza,
            "disparo_principal": self.disparo.to_dict() if self.disparo else None,
            "comuna_origen": self.comuna_origen,
            "temp_c": self.temp_c,
            "viento_kmh": self.viento_kmh,
            "temp_max_c": self.temp_max_c,
            "temp_min_c": self.temp_min_c,
            "humedad_min": self.humedad_min,
            "rafaga_max_kmh": self.rafaga_max_kmh,
            "uv_max": self.uv_max,
            "comunas": self.comunas,
            "con_lluvia": self.con_lluvia,
            "en_aviso": self.en_aviso,
            "en_critico": self.en_critico,
            "comunas_en_alerta": list(self.comunas_en_alerta),
            "modelo": self.modelo,
            "fuente": "open-meteo",
        }


def _mediana(valores: Sequence[float | None]) -> float | None:
    """Mediana de los valores presentes. `None` si no hay ninguno.

    Los `None` se **omiten**, no se cuentan como cero. Con tres comunas sin dato
    de temperatura, la mediana de las 33 restantes sigue siendo un número
    honesto; rellenarlas con ceros arrastraría la mediana hacia abajo y el
    widget diría que hace más frío del que hace.
    """
    presentes = [valor for valor in valores if valor is not None]
    if not presentes:
        return None
    return round(float(median(presentes)), 1)


def _maximo(valores: Sequence[float | None]) -> float | None:
    presentes = [valor for valor in valores if valor is not None]
    return round(max(presentes), 1) if presentes else None


def _minimo(valores: Sequence[float | None]) -> float | None:
    presentes = [valor for valor in valores if valor is not None]
    return round(min(presentes), 1) if presentes else None


def consolidar(
    pronosticos: Sequence[Pronostico],
    *,
    inicio: datetime,
    fin: datetime,
    modelo: str = "best_match",
) -> EstadoRegional | None:
    """Reduce los pronósticos comunales a un estado regional.

    Devuelve `None` con la lista vacía. No es lo mismo que un estado en calma:
    una lista vacía significa que la corrida no evaluó nada —la API no
    respondió, o la configuración se quedó sin comunas— y emitir un «todo
    tranquilo» con cero comunas detrás sería el fallo silencioso de siempre, esta
    vez pintado de verde en la barra superior.

    El punto de la región es el **centroide de los puntos consultados**, no el
    centro del `region_bbox`. Los dos son arbitrarios, pero el centroide describe
    algo real —dónde se midió— y cae siempre dentro de la nube de comunas, sin
    riesgo de aterrizar mar adentro cuando alguien recorte la lista por `.env`.
    """
    if not pronosticos:
        return None

    disparos = [disparo for item in pronosticos for disparo in item.disparos]
    principal = mas_grave(disparos)

    origen: str | None = None
    if principal is not None:
        origen = next(
            (item.comuna for item in pronosticos if principal in item.disparos),
            None,
        )

    en_alerta = sorted(
        (item for item in pronosticos if item.severidad != SEVERIDAD_NINGUNA),
        key=lambda item: (
            -ORDEN_SEVERIDAD.get(item.severidad, 0),
            item.comuna,
        ),
    )

    return EstadoRegional(
        inicio=inicio,
        fin=fin,
        lat=round(sum(item.lat for item in pronosticos) / len(pronosticos), 4),
        lon=round(sum(item.lon for item in pronosticos) / len(pronosticos), 4),
        severidad=principal.severidad if principal else SEVERIDAD_NINGUNA,
        amenaza=principal.amenaza if principal else None,
        disparo=principal,
        comuna_origen=origen,
        temp_c=_mediana([item.temp_actual_c for item in pronosticos]),
        viento_kmh=_mediana([item.viento_actual_kmh for item in pronosticos]),
        temp_max_c=_maximo([item.temp_max_c for item in pronosticos]),
        temp_min_c=_minimo([item.temp_min_c for item in pronosticos]),
        humedad_min=_minimo([item.humedad_min for item in pronosticos]),
        rafaga_max_kmh=_maximo([item.rafaga_max_kmh for item in pronosticos]),
        uv_max=_maximo([item.uv_max for item in pronosticos]),
        comunas=len(pronosticos),
        con_lluvia=sum(1 for item in pronosticos if item.hay_lluvia),
        en_aviso=sum(1 for item in pronosticos if item.severidad == SEVERIDAD_AVISO),
        en_critico=sum(1 for item in pronosticos if item.severidad == SEVERIDAD_CRITICA),
        comunas_en_alerta=tuple(item.comuna for item in en_alerta),
        modelo=modelo,
    )


def describir(estado: EstadoRegional) -> str:
    """Texto de `raw_events.text` para la fila regional.

    Nunca se muestra en el mapa —la fila regional está excluida de la capa— pero
    sí aparece en cualquier listado de señales crudas, así que tiene que
    explicarse sola y, sobre todo, tiene que dejar claro de qué habla: es un
    agregado de 36 puntos, no una observación de un lugar.
    """
    piezas: list[str] = [f"Estado meteorológico regional ({estado.comunas} comunas)"]
    if estado.temp_c is not None:
        piezas.append(f", mediana {estado.temp_c:.0f} °C")
    if estado.viento_kmh is not None:
        piezas.append(f", viento {estado.viento_kmh:.0f} km/h")
    piezas.append(".")

    frases = ["".join(piezas)]
    if estado.disparo is not None and estado.comuna_origen is not None:
        frases.append(
            f"Peor caso en {estado.comuna_origen}: {estado.disparo.texto}."
        )
        frases.append(
            f"{estado.en_critico} comuna(s) en condición crítica y "
            f"{estado.en_aviso} en aviso."
        )
    else:
        frases.append("Ninguna comuna cruza un umbral táctico.")

    frases.append(
        f"Agregado de Open-Meteo ({estado.modelo}); no es una alerta oficial: "
        f"las declara SENAPRED."
    )
    return " ".join(frases)
