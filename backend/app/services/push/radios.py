"""Radio de aviso por categoría (§K, 2026-10-05).

Hasta el 2026-10-05 había un solo radio por suscripción (5 km por defecto) y la
PWA no lo dejaba cambiar. Un incendio a 5 km vale el aviso; un corte de luz a
5 km casi nunca. Ahora cada categoría tiene su radio, que cada persona elige
con un deslizador; `0` apaga esa categoría.

Las categorías son las familias del motor (`models.enums.INCIDENT_FAMILY`) más
los cortes de agua, que no son incidentes. Se guardan en
`push_subscriptions.radios` sólo las que la persona tocó: una categoría ausente
usa el valor por defecto del servidor (`PUSH_RADIO_*_M`), así que cambiar un
valor por defecto alcanza a quien nunca lo cambió.

Los radios se aplican igual desde la ubicación del teléfono y desde cada lugar
guardado («Casa», «Trabajo»): un incendio a 4 km de Casa avisa igual que a
4 km de donde uno está. Un radio por lugar y por categoría serían 20
deslizadores en el teléfono, y nadie los ajusta.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.config import settings
from app.models.enums import IncidentType, family_of_incident

FIRE = "fire"
TRAFFIC = "traffic"
POWER = "power"
HYDRO = "hydro"
OTHER = "other"
WATER = "water"

#: En el orden en que los muestra la PWA.
CATEGORIAS: tuple[str, ...] = (FIRE, TRAFFIC, POWER, HYDRO, OTHER, WATER)

ETIQUETAS: dict[str, str] = {
    FIRE: "Incendios",
    TRAFFIC: "Accidentes",
    POWER: "Cortes de luz",
    HYDRO: "Inundaciones y derrumbes",
    OTHER: "Otras emergencias",
    WATER: "Cortes de agua",
}

#: Un radio distinto de 0 no puede ser menor que esto: la ubicación se guarda
#: redondeada a ~110 m y el punto del incidente es, en el peor caso, una calle.
RADIO_MINIMO_M = 300.0
RADIO_MAXIMO_M = 20_000.0


def por_defecto() -> dict[str, float]:
    """Los radios del servidor, por categoría."""
    return {
        FIRE: settings.PUSH_RADIO_FIRE_M,
        TRAFFIC: settings.PUSH_RADIO_TRAFFIC_M,
        POWER: settings.PUSH_RADIO_POWER_M,
        HYDRO: settings.PUSH_RADIO_HYDRO_M,
        OTHER: settings.PUSH_RADIO_OTHER_M,
        WATER: settings.PUSH_RADIO_WATER_M,
    }


def categoria_de(incident_type: IncidentType) -> str:
    """La categoría de aviso de un incidente: su familia, o `other`."""
    familia = family_of_incident(incident_type)
    return familia if familia in CATEGORIAS and familia != WATER else OTHER


def validar_radio(valor: Any) -> float:
    """0 (apagado) o un radio entre 300 m y 20 km, redondeado a metros enteros."""
    try:
        metros = float(valor)
    except (TypeError, ValueError) as exc:
        raise ValueError("el radio tiene que ser un número de metros") from exc
    if metros != metros or metros < 0:  # NaN o negativo
        raise ValueError("el radio no puede ser negativo")
    if metros == 0:
        return 0.0
    if metros < RADIO_MINIMO_M or metros > RADIO_MAXIMO_M:
        raise ValueError(
            f"el radio va de {RADIO_MINIMO_M:.0f} a {RADIO_MAXIMO_M:.0f} m, o 0 para apagar"
        )
    return float(round(metros))


def normalizar(radios: Mapping[str, Any] | None) -> dict[str, float]:
    """Lo que manda la PWA → lo que se guarda. Lanza `ValueError` si algo no sirve."""
    if not radios:
        return {}
    salida: dict[str, float] = {}
    for categoria, valor in radios.items():
        if categoria not in CATEGORIAS:
            raise ValueError(f"categoría desconocida: {categoria!r}")
        salida[categoria] = validar_radio(valor)
    return salida


def efectivos(guardados: Mapping[str, Any] | None) -> dict[str, float]:
    """Los radios que valen para una suscripción: los suyos sobre los del servidor."""
    radios = por_defecto()
    for categoria, valor in (guardados or {}).items():
        if categoria in radios:
            try:
                radios[categoria] = validar_radio(valor)
            except ValueError:
                continue
    return radios


__all__ = [
    "CATEGORIAS",
    "ETIQUETAS",
    "RADIO_MAXIMO_M",
    "RADIO_MINIMO_M",
    "WATER",
    "categoria_de",
    "efectivos",
    "normalizar",
    "por_defecto",
    "validar_radio",
]
