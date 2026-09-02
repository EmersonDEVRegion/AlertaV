"""Ventana de congestión estimada para un siniestro en una vía conocida.

Qué problema resuelve
---------------------
Sin la API de Waze, AlertaV puede decir **dónde** hay un accidente pero no
cuánto va a durar el taco. Para alguien que está por salir, eso es la mitad del
dato que necesita: la pregunta real no es «hubo un choque en Av. España», es
«¿me conviene salir ahora o espero?».

Esto no mide tráfico. **Estima** cuánto suele tardar en despejarse un siniestro
en una vía determinada, a partir de una tabla declarada, y lo dice como lo que
es: una estimación con su base a la vista.

Por qué una tabla y no un modelo
--------------------------------
Se evaluó preguntarle la ventana al modelo por cada evento. Se descartó, y por
una razón que no es el costo: **un número que nadie puede auditar no se puede
defender**. Si alguien llega tarde a algo por confiar en «espere congestión
hasta las 15:40», tiene que existir una línea de este archivo que explique de
dónde salió ese 15:40 y una persona que pueda corregirla. Una respuesta
generada por un modelo no ofrece ninguna de las dos cosas.

La tabla empieza tosca a propósito. Los números de abajo son de oficio, no
medidos, y están puestos para ser corregidos con lo que se vaya viendo. Ese
ajuste es un cambio de una línea con historial en git, que es exactamente la
propiedad que se buscaba.

Qué NO estima
-------------
Nada fuera de la tabla. Una calle que no está declarada devuelve `None`, y el
panel no muestra ventana. Inventar una duración genérica para cualquier calle
sería el mismo error que geocodificar al centroide comunal: parece un dato y no
lo es. Es preferible callar en la mayoría de las calles y acertar en las diez
que de verdad generan taco en la región.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

CHILE_TZ = ZoneInfo("America/Santiago")


@dataclass(frozen=True, slots=True)
class Arteria:
    """Una vía cuyo comportamiento en congestión se conoce lo suficiente."""

    label: str
    #: Minutos típicos hasta despejar, fuera de hora punta.
    despeje_min: int
    #: Ídem en hora punta. No es el doble por capricho: en punta no sólo tarda
    #: más el despeje, además la cola tarda en disolverse después.
    despeje_punta_min: int
    #: Fragmentos normalizados que identifican la vía en el texto extraído.
    #: Varios porque la prensa la nombra de maneras distintas.
    alias: tuple[str, ...]
    #: Por qué esta vía está en la tabla. Se muestra al usuario como base de la
    #: estimación, y obliga a que cada fila tenga una justificación escrita.
    motivo: str


#: Las vías de la V Región donde un siniestro genera congestión medible.
#:
#: El criterio de entrada es la **falta de alternativa**, no el volumen: una
#: avenida con calles paralelas se descongestiona sola porque el tráfico se
#: reparte. Av. España está acá porque junto con Borgoño son las únicas dos
#: conexiones entre Valparaíso y Viña por la costa: cortar una vuelca todo sobre
#: la otra. Ruta 68 está porque es la conexión con Santiago y no tiene paralela.
ARTERIAS: tuple[Arteria, ...] = (
    Arteria(
        label="Ruta 68",
        despeje_min=60,
        despeje_punta_min=110,
        alias=("ruta 68", "ruta68", "autopista troncal sur"),
        motivo="conexión con Santiago, sin vía paralela",
    ),
    Arteria(
        label="Av. España",
        despeje_min=45,
        despeje_punta_min=90,
        alias=("av espana", "avenida espana", "espana"),
        motivo="una de las dos únicas conexiones Valparaíso–Viña por la costa",
    ),
    Arteria(
        label="Av. Borgoño",
        despeje_min=40,
        despeje_punta_min=80,
        alias=("borgono", "av borgono", "avenida borgono"),
        motivo="la otra conexión costera Valparaíso–Viña",
    ),
    Arteria(
        label="Vía Las Palmas",
        despeje_min=50,
        despeje_punta_min=95,
        alias=("las palmas", "via las palmas", "subida las palmas"),
        motivo="acceso a Viña por el interior, de curvas y sin bermas",
    ),
    Arteria(
        label="Troncal Sur",
        despeje_min=45,
        despeje_punta_min=85,
        alias=("troncal sur", "ruta 60", "ruta 60 ch"),
        motivo="eje Viña–Quilpué–Villa Alemana, alterna sólo con Agua Santa",
    ),
    Arteria(
        label="Camino Internacional",
        despeje_min=40,
        despeje_punta_min=70,
        alias=("camino internacional",),
        motivo="conexión Viña–Con Cón, calzada única en buena parte",
    ),
    Arteria(
        label="Agua Santa",
        despeje_min=35,
        despeje_punta_min=70,
        alias=("agua santa", "subida agua santa"),
        motivo="única alternativa a Troncal Sur, de pendiente y curvas",
    ),
    Arteria(
        label="Av. Argentina",
        despeje_min=30,
        despeje_punta_min=55,
        alias=("av argentina", "avenida argentina"),
        motivo="eje de entrada al plan de Valparaíso",
    ),
    Arteria(
        label="Av. Pedro Montt",
        despeje_min=30,
        despeje_punta_min=55,
        alias=("pedro montt",),
        motivo="eje principal del plan de Valparaíso",
    ),
    Arteria(
        label="Ruta 5 Norte",
        despeje_min=55,
        despeje_punta_min=90,
        alias=("ruta 5", "ruta 5 norte", "panamericana"),
        motivo="eje norte–sur, sin alternativa en el tramo regional",
    ),
)

#: Hora punta en días hábiles, en hora local de Chile.
#:
#: Declaradas y no inferidas: no hay datos de flujo en este proyecto para
#: derivarlas, y fingir que sí los hay sería peor que declararlas.
PUNTA_MANANA = (7, 30, 9, 30)
PUNTA_TARDE = (17, 30, 20, 0)


@dataclass(frozen=True, slots=True)
class VentanaCongestion:
    """Estimación de cuánto va a durar el taco. NO es una medición."""

    arteria: str
    motivo: str
    desde: datetime
    hasta: datetime
    en_punta: bool
    #: Minutos estimados de despeje. Se expone para que la ficha pueda explicar
    #: el número en vez de sólo mostrar dos relojes.
    duracion_min: int

    @property
    def duracion(self) -> timedelta:
        return self.hasta - self.desde


def _plano(texto: str) -> str:
    limpio = unicodedata.normalize("NFD", str(texto or "").lower())
    sin_tildes = "".join(c for c in limpio if unicodedata.category(c) != "Mn")
    return " ".join(sin_tildes.replace(".", " ").split())


def arteria_de(nombre: str | None) -> Arteria | None:
    """Reconoce la vía en un nombre de calle. None si no está en la tabla.

    Los alias se prueban del más largo al más corto: «troncal sur» tiene que
    ganarle a «ruta 60» si el texto trae los dos, y sobre todo «av espana» no
    puede perder contra un alias corto que aparezca antes en la tabla.
    """
    if not nombre:
        return None
    plano = _plano(nombre)
    if not plano:
        return None

    candidatas = [
        (alias, arteria)
        for arteria in ARTERIAS
        for alias in arteria.alias
        if alias in plano
    ]
    if not candidatas:
        return None
    return max(candidatas, key=lambda par: len(par[0]))[1]


def es_hora_punta(momento: datetime) -> bool:
    """¿Cae en hora punta de día hábil, en hora de Chile?

    La conversión a hora local es obligatoria y no cosmética: todo en esta base
    vive en UTC, y en Chile eso son tres o cuatro horas de diferencia según la
    época del año. Evaluar la punta sobre el reloj UTC pondría la punta de la
    mañana a mitad de la madrugada.
    """
    local = momento.astimezone(CHILE_TZ)
    if local.weekday() >= 5:
        return False

    minutos = local.hour * 60 + local.minute
    for h1, m1, h2, m2 in (PUNTA_MANANA, PUNTA_TARDE):
        if h1 * 60 + m1 <= minutos <= h2 * 60 + m2:
            return True
    return False


def estimar(calle: str | None, ocurrido_en: datetime) -> VentanaCongestion | None:
    """Ventana estimada, o None si la vía no está en la tabla.

    `None` es la respuesta correcta y la más frecuente: la tabla cubre diez vías
    y la región tiene miles de calles. Es deliberado — ver el encabezado del
    módulo.
    """
    arteria = arteria_de(calle)
    if arteria is None:
        return None

    punta = es_hora_punta(ocurrido_en)
    minutos = arteria.despeje_punta_min if punta else arteria.despeje_min

    return VentanaCongestion(
        arteria=arteria.label,
        motivo=arteria.motivo,
        desde=ocurrido_en,
        hasta=ocurrido_en + timedelta(minutes=minutos),
        en_punta=punta,
        duracion_min=minutos,
    )


__all__ = [
    "ARTERIAS",
    "Arteria",
    "VentanaCongestion",
    "arteria_de",
    "es_hora_punta",
    "estimar",
]
