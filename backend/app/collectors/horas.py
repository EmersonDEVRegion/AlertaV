"""La hora que la prensa declara en el texto, cuando la declara.

Para qué existe
---------------
Un accidente tiene dos horas y no son la misma. Está la hora en que **se
publicó** la nota —que es la que hoy termina en `Event.timestamp`— y la hora en
que **ocurrió el hecho**, que es la que le importa a alguien que va a salir a la
calle. Entre las dos puede haber una hora larga: la prensa escribe después.

Esa distancia es justamente lo que vuelve inútil una ventana de congestión
calculada sobre la hora de publicación. «Espere congestión hasta las 16:40»
cuando el choque fue a las 14:10 y ya se despejó es peor que no decir nada:
manda a alguien a evitar una calle que está libre.

Cómo escribe la prensa chilena
------------------------------
Casi nunca con reloj. Escribe «cerca de las 14:30 horas», «aproximadamente al
mediodía», «durante la madrugada de este martes», «en horas de la tarde». Por
eso este módulo devuelve una hora **y su precisión**, y nunca finge que una
franja es un instante:

* `EXACTA` — «a las 14:30». El texto dio hora y minuto.
* `APROXIMADA` — «cerca de las 14 horas». Hay hora, no hay minuto.
* `FRANJA` — «durante la tarde». Hay un tramo del día y nada más; la hora que
  se devuelve es su centro declarado, no una medición.

Quien consuma esto tiene que propagar la precisión hasta la pantalla. Una
ventana construida sobre una `FRANJA` no puede presentarse con la misma cara
que una construida sobre una `EXACTA`.

Qué NO hace
-----------
No devuelve una fecha. Devuelve la hora del día, y sólo eso: el día lo pone
quien llama, a partir del `timestamp` del evento, porque es el único que sabe si
la nota dice «este martes» refiriéndose a hoy o a la semana pasada. Mezclar las
dos cosas acá produciría fechas inventadas con aspecto de dato.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum


class Precision(str, Enum):
    EXACTA = "exacta"
    APROXIMADA = "aproximada"
    FRANJA = "franja"


@dataclass(frozen=True, slots=True)
class HoraDeclarada:
    """La hora del día que el texto declara, con cuánto se le puede creer."""

    hora: int
    minuto: int
    precision: Precision
    #: El fragmento que la produjo. Va a `raw_data` para poder auditar la
    #: lectura sin volver a correr el extractor sobre el texto completo.
    fragmento: str

    @property
    def es_estimacion(self) -> bool:
        return self.precision is not Precision.EXACTA


def _plano(texto: str) -> str:
    """Minúsculas y sin tildes. La prensa escribe «madrugada» y «Madrugada»."""
    limpio = unicodedata.normalize("NFD", str(texto or "").lower())
    return "".join(c for c in limpio if unicodedata.category(c) != "Mn")


#: Franjas del día, con la hora que las representa.
#:
#: Los valores son convencionales y están declarados, no derivados: «la tarde»
#: no tiene una hora correcta y cualquier número que se elija es una decisión.
#: Se eligen los centros habituales del uso chileno, y se marcan `FRANJA` para
#: que nadie los confunda con una lectura del texto.
_FRANJAS: tuple[tuple[str, int, int], ...] = (
    ("madrugada", 3, 30),
    ("mediodia", 12, 30),
    ("medio dia", 12, 30),
    ("manana", 9, 30),
    ("tarde", 16, 0),
    ("noche", 21, 30),
)

#: «de la tarde» convierte un 2 en las 14. Sin esto, «a las 2 de la tarde»
#: entraría como las 02:00 y la ventana de congestión saldría doce horas
#: desplazada — el peor error posible acá, porque no se ve raro.
_SUFIJOS: tuple[tuple[str, int], ...] = (
    ("de la madrugada", 0),
    ("de la manana", 0),
    ("del mediodia", 12),
    ("de la tarde", 12),
    ("de la noche", 12),
)

#: Marcas de que el número es una aproximación del propio redactor.
_APROXIMADORES = (
    "aproximadamente",
    "cerca de",
    "alrededor de",
    "pasadas",
    "pasado",
    "cerca a",
    "en torno a",
)

#: `a las 14:30`, `las 14.30 horas`, `a las 9 hrs`, `a las 2 de la tarde`.
_RELOJ = re.compile(
    r"(?:a\s+)?las?\s+(?P<hora>\d{1,2})"
    r"(?:\s*[:.]\s*(?P<minuto>\d{2}))?"
    r"\s*(?:horas?|hrs?\.?|h\b)?",
)


def parse_hora_declarada(texto: str) -> HoraDeclarada | None:
    """Hora del día que el texto declara. None si no declara ninguna.

    None es un resultado legítimo y frecuente: muchas notas no dicen la hora, y
    inventarla a partir de la publicación es exactamente lo que este módulo
    existe para no hacer.
    """
    plano = _plano(texto)
    if not plano:
        return None

    reloj = _buscar_reloj(plano, texto)
    if reloj is not None:
        return reloj

    return _buscar_franja(plano)


def _buscar_reloj(plano: str, original: str) -> HoraDeclarada | None:
    for match in _RELOJ.finditer(plano):
        hora = int(match.group("hora"))
        minuto = int(match.group("minuto") or 0)
        if hora > 23 or minuto > 59:
            continue

        # El sufijo se mira DESPUÉS del número y en una ventana corta: «a las 2
        # de la tarde» sí, pero «a las 2, tras una tarde de lluvia» no.
        cola = plano[match.end() : match.end() + 24]
        for sufijo, desplazamiento in _SUFIJOS:
            if cola.lstrip().startswith(sufijo):
                if desplazamiento and hora < 12:
                    hora += desplazamiento
                # «de la madrugada» con un 12 son las 00, no las 12.
                if sufijo == "de la madrugada" and hora == 12:
                    hora = 0
                break

        # El aproximador va delante: «cerca de las 14 horas».
        antes = plano[max(0, match.start() - 26) : match.start()]
        aproximado = any(marca in antes for marca in _APROXIMADORES)

        # Sin minutos, la hora es un redondeo del redactor aunque no lo diga.
        precision = (
            Precision.EXACTA
            if match.group("minuto") and not aproximado
            else Precision.APROXIMADA
        )
        return HoraDeclarada(
            hora=hora,
            minuto=minuto,
            precision=precision,
            fragmento=original[match.start() : match.end()].strip() or match.group(0),
        )
    return None


def _buscar_franja(plano: str) -> HoraDeclarada | None:
    # Se recorre en el orden declarado y no por posición en el texto: «madrugada»
    # antes que «manana» porque «durante la madrugada de esta mañana» nombra las
    # dos y la primera es la que fecha el hecho.
    for nombre, hora, minuto in _FRANJAS:
        if nombre in plano:
            return HoraDeclarada(
                hora=hora,
                minuto=minuto,
                precision=Precision.FRANJA,
                fragmento=nombre,
            )
    return None


__all__ = ["HoraDeclarada", "Precision", "parse_hora_declarada"]
