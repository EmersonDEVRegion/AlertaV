"""Política de riesgo de inundación: de una serie horaria a un flag. Módulo puro.

Acá vive la única pregunta interesante de este collector. Saber que van a caer
40 mm no dice nada por sí solo: 40 mm repartidos en dos días son un invierno
normal en Valparaíso y 40 mm en tres horas son quebradas desbordadas y la Ruta 68
cortada. Lo que importa es la **intensidad** y el **acumulado en ventana corta**,
no el total.

Las tres reglas
---------------

============================  =========  ==========================================
Regla                         Defecto    Qué pretende capturar
============================  =========  ==========================================
Intensidad horaria máxima     5 mm/h     Chubasco fuerte: el drenaje urbano se
                                         satura y el agua corre por la calle.
Acumulado móvil en 3 h        15 mm      Lluvia sostenida: el suelo ya no infiltra
                                         y las quebradas empiezan a cargar.
Acumulado en la ventana 24 h  40 mm      Temporal: saturación del terreno, riesgo
                                         de remoción en masa en los cerros.
============================  =========  ==========================================

Cualquiera de las tres levanta `riesgo_inundacion`. Se dispara con OR y no con
AND a propósito: los tres fenómenos son distintos y ninguno necesita a los otros
dos para cortar un camino.

**Estos números son un punto de partida calibrable, no un umbral oficial.** No
salen de una norma de la DMC ni de SENAPRED —que publican categorías
cualitativas, no cortes numéricos para inundación urbana— sino de la geografía
del caso: cerros con pendiente fuerte, quebradas canalizadas y drenaje urbano
antiguo, donde el problema aparece con intensidades más bajas que en una ciudad
plana. Se dejan en `settings` para poder moverlos sin desplegar, y la forma
correcta de fijarlos es contrastar los eventos de esta capa con los avisos de vía
cortada de Transporte Informa a lo largo de un invierno. Hasta entonces, el flag
es una hipótesis explícita y así hay que leerlo.

Por qué la probabilidad NO filtra
---------------------------------

`precipitation_probability` se publica en la salida —el producto lo pidió— pero
**no participa de la decisión**. Un modelo que anuncia 20 mm/h con 30 % de
probabilidad es justo el caso que una app de emergencias tiene que mostrar; usar
la probabilidad como veto significaría esconder el escenario grave por ser el
menos probable. La certeza es una dimensión aparte, y en este sistema se comunica
aparte: la confianza de la fuente (0.10) y el `nivel` de la señal. Es la misma
separación que `style_for` hace en `models/enums.py` entre color (cuánto sabemos)
e ícono (de qué se trata).

Además hay una razón práctica: no todos los modelos de Open-Meteo publican
probabilidad. Si filtrara, una comuna servida por un modelo sin ese campo
perdería el flag en silencio.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.collectors.weather.comunas import Comuna

#: Hora de pared chilena, sólo para el texto legible. Se usa `zoneinfo` y no un
#: desfase fijo por lo mismo que en `outage_parser` y `csn_parser`: Chile cambia
#: de UTC-4 a UTC-3 en septiembre y ha movido las fechas de cambio varias veces.
CHILE_TZ = ZoneInfo("America/Santiago")

#: Nivel de la señal. Es vocabulario de PRESENTACIÓN: decide el color y el grosor
#: de la capa. El contrato que el frontend debe leer para decidir *si hay riesgo*
#: es el booleano `riesgo_inundacion`, no esto.
NIVEL_SECO = "seco"
NIVEL_LLUVIA = "lluvia"
NIVEL_RIESGO = "riesgo"
NIVEL_RIESGO_ALTO = "riesgo_alto"

#: Multiplicador de la intensidad horaria a partir del cual una sola regla ya
#: basta para `riesgo_alto`. 2× el umbral (10 mm/h por defecto) es un chubasco
#: que no necesita corroboración de las otras dos reglas para ser grave.
_FACTOR_ALTO = 2.0


@dataclass(frozen=True, slots=True)
class PuntoHorario:
    """Un paso horario del pronóstico.

    `mm` es `float | None` y esa distinción es la que separa "no va a llover" de
    "el modelo no entregó el dato". Colapsar `None` a `0.0` acá haría que una
    respuesta con el campo vacío se leyera como una tarde seca — el modo de fallo
    que este proyecto persigue en todas las fuentes: cero eventos con estado
    `success`. Ver `SerieComunal.datos_validos`.
    """

    momento: datetime
    mm: float | None
    probabilidad: int | None = None


@dataclass(frozen=True, slots=True)
class Umbrales:
    """Parámetros de la política. Se pasan por argumento para poder testearlos."""

    intensidad_mm_h: float = 5.0
    acumulado_3h_mm: float = 15.0
    acumulado_24h_mm: float = 40.0
    #: Milímetros en la ventana por debajo de los cuales la comuna no genera
    #: evento. 0.2 mm en 24 h es llovizna de modelo: guardar 36 filas por hora
    #: para decir eso llenaría `raw_events` de ruido y no cambiaría ningún mapa.
    mm_minimo_ingesta: float = 0.2
    ventana_horas: int = 24

    @classmethod
    def from_settings(cls) -> Umbrales:
        from app.core.config import settings

        return cls(
            intensidad_mm_h=settings.OPENMETEO_INTENSITY_MM_H,
            acumulado_3h_mm=settings.OPENMETEO_ACCUM_3H_MM,
            acumulado_24h_mm=settings.OPENMETEO_ACCUM_24H_MM,
            mm_minimo_ingesta=settings.OPENMETEO_MIN_INGEST_MM,
            ventana_horas=settings.OPENMETEO_WINDOW_HOURS,
        )


@dataclass(frozen=True, slots=True)
class Pronostico:
    """Lo que este collector sabe de una comuna. Es el contrato de salida.

    `to_dict()` produce el JSON estandarizado y ligero que viaja en
    `raw_data["_weather"]` y que un endpoint de mapa puede servir tal cual, sin
    volver a consultar Open-Meteo ni recalcular nada.
    """

    comuna: str
    lat: float
    lon: float
    inicio: datetime
    fin: datetime
    horas: int
    mm_total: float
    mm_hora_max: float
    mm_3h_max: float
    hora_pico: datetime | None
    probabilidad_max: int | None
    horas_con_lluvia: int
    riesgo_inundacion: bool
    nivel: str
    motivos: tuple[str, ...]
    modelo: str

    @property
    def hay_lluvia(self) -> bool:
        """¿Vale la pena guardar esta comuna como señal?"""
        return self.nivel != NIVEL_SECO

    def to_dict(self) -> dict[str, Any]:
        """JSON plano y serializable. Nada de datetimes ni de enums acá dentro.

        Las fechas salen en ISO-8601 UTC porque este diccionario termina en una
        columna JSONB, y un `datetime` no es serializable a JSON. La lección ya
        se pagó una vez en `seismic_row`: asyncpg tampoco convierte de vuelta el
        texto a `timestamptz`, así que quien lo relea tiene que reconstruirlo.
        """
        return {
            "comuna": self.comuna,
            "lat": self.lat,
            "lon": self.lon,
            "ventana_horas": self.horas,
            "inicio": self.inicio.isoformat(),
            "fin": self.fin.isoformat(),
            "mm_total": self.mm_total,
            "mm_hora_max": self.mm_hora_max,
            "mm_3h_max": self.mm_3h_max,
            "hora_pico": self.hora_pico.isoformat() if self.hora_pico else None,
            "probabilidad_max": self.probabilidad_max,
            "horas_con_lluvia": self.horas_con_lluvia,
            "riesgo_inundacion": self.riesgo_inundacion,
            "nivel": self.nivel,
            "motivos": list(self.motivos),
            "modelo": self.modelo,
            "fuente": "open-meteo",
        }


def piso_horario(momento: datetime) -> datetime:
    """Trunca a la hora en curso, en UTC.

    Es el ancla de todo el módulo: marca el inicio de la ventana, el `timestamp`
    del evento y la clave horaria del `external_id`. Se trunca hacia **abajo**
    —no se redondea— por una razón concreta: `EventCreate` rechaza timestamps más
    de `INGEST_FUTURE_TOLERANCE_SECONDS` (5 min) en el futuro. Redondear hacia
    arriba pondría el evento hasta 59 minutos adelante y la validación lo
    tumbaría, con el collector reportando todo rechazado sin explicar por qué.
    """
    return momento.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def recortar_ventana(
    puntos: Sequence[PuntoHorario], *, desde: datetime, horas: int
) -> list[PuntoHorario]:
    """Los pasos horarios de `[desde, desde + horas)`, en orden.

    Open-Meteo devuelve el día completo desde las 00:00, así que la mitad de la
    respuesta suele ser pasado. Se descarta: a las 22:00 no interesa que a las
    04:00 de esta mañana llovió, interesa la madrugada que viene.
    """
    fin = desde + timedelta(hours=horas)
    dentro = [punto for punto in puntos if desde <= punto.momento < fin]
    return sorted(dentro, key=lambda punto: punto.momento)


def acumulado_maximo(
    puntos: Sequence[PuntoHorario], *, horas: int
) -> tuple[float, datetime | None]:
    """Máximo acumulado en una ventana móvil de `horas`, y cuándo empieza.

    La ventana se define por **timestamp** y no por número de posiciones. Con una
    serie horaria completa da lo mismo, pero si el modelo entrega pasos de 3 h
    —los hay— o si falta una hora, contar posiciones sumaría seis horas de lluvia
    creyendo que son tres y el flag saltaría solo. Sumar por tiempo no puede
    equivocarse en eso.
    """
    mejor = 0.0
    inicio: datetime | None = None
    for indice, punto in enumerate(puntos):
        limite = punto.momento + timedelta(hours=horas)
        total = sum(
            otro.mm or 0.0
            for otro in puntos[indice:]
            if otro.momento < limite
        )
        if total > mejor:
            mejor, inicio = total, punto.momento
    return round(mejor, 1), inicio


def evaluar(
    comuna: Comuna,
    puntos: Sequence[PuntoHorario],
    *,
    ahora: datetime | None = None,
    umbrales: Umbrales | None = None,
    modelo: str = "best_match",
) -> Pronostico:
    """Aplica la política a la serie de una comuna. Sin red, sin base de datos.

    Es el corazón testeable del collector: se le pasan puntos armados a mano y se
    comprueba el flag. `openmeteo_worker` no decide nada que no esté acá.
    """
    reglas = umbrales or Umbrales()
    inicio = piso_horario(ahora or datetime.now(UTC))
    ventana = recortar_ventana(puntos, desde=inicio, horas=reglas.ventana_horas)

    validos = [punto for punto in ventana if punto.mm is not None]
    mm_total = round(sum(punto.mm or 0.0 for punto in validos), 1)
    mm_hora_max = round(max((punto.mm or 0.0 for punto in validos), default=0.0), 1)
    mm_3h_max, _ = acumulado_maximo(ventana, horas=3)

    pico = max(validos, key=lambda punto: punto.mm or 0.0, default=None)
    hora_pico = pico.momento if pico and (pico.mm or 0.0) > 0 else None

    probabilidades = [
        punto.probabilidad for punto in ventana if punto.probabilidad is not None
    ]
    probabilidad_max = max(probabilidades) if probabilidades else None

    # 0.1 mm es el paso de publicación de la variable: por debajo de eso el
    # modelo está diciendo "traza", no "hora con lluvia".
    horas_con_lluvia = sum(1 for punto in validos if (punto.mm or 0.0) >= 0.1)

    motivos: list[str] = []
    if mm_hora_max >= reglas.intensidad_mm_h:
        motivos.append(
            f"intensidad {mm_hora_max:.1f} mm/h ≥ {reglas.intensidad_mm_h:.1f} mm/h"
        )
    if mm_3h_max >= reglas.acumulado_3h_mm:
        motivos.append(
            f"acumulado en 3 h {mm_3h_max:.1f} mm ≥ {reglas.acumulado_3h_mm:.1f} mm"
        )
    if mm_total >= reglas.acumulado_24h_mm:
        motivos.append(
            f"acumulado en {reglas.ventana_horas} h {mm_total:.1f} mm "
            f"≥ {reglas.acumulado_24h_mm:.1f} mm"
        )

    return Pronostico(
        comuna=comuna.nombre,
        lat=comuna.lat,
        lon=comuna.lon,
        inicio=inicio,
        fin=inicio + timedelta(hours=reglas.ventana_horas),
        horas=reglas.ventana_horas,
        mm_total=mm_total,
        mm_hora_max=mm_hora_max,
        mm_3h_max=mm_3h_max,
        hora_pico=hora_pico,
        probabilidad_max=probabilidad_max,
        horas_con_lluvia=horas_con_lluvia,
        riesgo_inundacion=bool(motivos),
        nivel=_nivel(mm_total, mm_hora_max, motivos, reglas),
        motivos=tuple(motivos),
        modelo=modelo,
    )


def _nivel(
    mm_total: float, mm_hora_max: float, motivos: Sequence[str], reglas: Umbrales
) -> str:
    if not motivos:
        return NIVEL_LLUVIA if mm_total >= reglas.mm_minimo_ingesta else NIVEL_SECO
    if len(motivos) >= 2 or mm_hora_max >= reglas.intensidad_mm_h * _FACTOR_ALTO:
        return NIVEL_RIESGO_ALTO
    return NIVEL_RIESGO


def describir(pronostico: Pronostico) -> str:
    """Texto legible en español para el popup del mapa y para `raw_events.text`.

    Dos detalles deliberados:

    * La hora del pico se expresa en **hora de Chile**, porque la lee una
      persona. Todo lo demás del sistema es UTC y sigue siéndolo en el payload.
    * Termina aclarando que es un pronóstico y que las alertas las declara
      SENAPRED. Es la misma cautela con que el collector del USGS aclara que el
      protocolo de tsunami no es una alerta de tsunami: el sistema no puede
      insinuar que declaró algo que no le corresponde declarar.

    El formato ``Comuna: X.`` no es decorativo: es el patrón que
    `communes._TEXT_COMMUNE_PATTERNS` sabe leer.
    """
    partes = [
        f"Lluvia pronosticada en {pronostico.comuna}: "
        f"{pronostico.mm_total:.1f} mm en {pronostico.horas} h, "
        f"máximo de {pronostico.mm_hora_max:.1f} mm/h"
    ]
    if pronostico.hora_pico is not None:
        local = pronostico.hora_pico.astimezone(CHILE_TZ)
        partes.append(f" hacia las {local:%H:%M} h")
    if pronostico.probabilidad_max is not None:
        partes.append(f" (probabilidad {pronostico.probabilidad_max} %)")
    partes.append(".")

    frases = ["".join(partes)]
    if pronostico.riesgo_inundacion:
        frases.append(
            "RIESGO DE INUNDACIÓN: " + "; ".join(pronostico.motivos) + "."
        )
    frases.append(f"Comuna: {pronostico.comuna}.")
    frases.append(
        f"Pronóstico de Open-Meteo ({pronostico.modelo}); no es una alerta "
        f"oficial: las declara SENAPRED."
    )
    return " ".join(frases)
