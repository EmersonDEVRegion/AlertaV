"""Lo que se muestra del tránsito del MTT: qué está vigente y cómo se nombra.

Lo usan el endpoint de la capa (`GET /events/transito/geojson`), la lista de
«Ahora» (el mismo endpoint) y el notificador. Funciones puras: sin base ni red.

Cada subtipo vive lo que tarda en dejar de ser cierto sin que nadie lo diga:

* congestión y tránsito lento, 90 min (un taco se disuelve solo);
* semáforo apagado, 6 h (alguien tiene que repararlo);
* pista ocupada, 12 h (como los cortes del MTT);
* marcha, 4 h: se mueve o se disuelve, y si el MTT deja de informarla ya no se
  sabe dónde está. Un tuit nuevo de la misma marcha cierra el anterior
  (`transito_x.reemplazar_marchas`): queda el último punto informado;
* normalidad, 45 min: es el verde que confirma que se despejó, no un estado.

Y se cierra antes si el MTT avisa la normalidad de esa vía (`_mtt_fin`, ver
`collectors/traffic/transito_x.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

VIGENCIA: dict[str, timedelta] = {
    "congestion": timedelta(minutes=90),
    "lento": timedelta(minutes=90),
    "semaforo": timedelta(hours=6),
    "pista_ocupada": timedelta(hours=12),
    "marcha": timedelta(hours=4),
    "normalidad": timedelta(minutes=45),
}

#: Cómo se nombra cada subtipo en la lista, la ficha y el aviso.
NOMBRE: dict[str, str] = {
    "congestion": "Congestión",
    "lento": "Tránsito lento",
    "semaforo": "Semáforo apagado",
    "pista_ocupada": "Pista ocupada",
    "marcha": "Marcha",
    "normalidad": "Tránsito normal",
}

#: Los que pueden avisar por push (con la categoría encendida). «Lento» no:
#: es lo más frecuente y no cambia qué hacer. «Normalidad» tampoco: es una
#: buena noticia que no necesita interrumpir a nadie.
CON_AVISO = frozenset({"congestion", "semaforo", "pista_ocupada", "marcha"})

#: Una congestión avisa sólo si se sabe el tramo: «congestión en Av. España»
#: sin más es toda la costanera, y a 2 km de cualquier punto de ella.
METODOS_CON_TRAMO = frozenset({"tramo", "tramo_esquinas"})


def bloque_mtt(raw: Any) -> dict[str, Any] | None:
    """El bloque `_mtt` de una señal, o None si no es del tránsito nuevo."""
    if not isinstance(raw, dict):
        return None
    mtt = raw.get("_mtt")
    if not isinstance(mtt, dict) or mtt.get("tipo") not in VIGENCIA:
        return None
    return mtt


def _aware(momento: datetime) -> datetime:
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


def vence(subtipo: str, desde: datetime) -> datetime:
    return _aware(desde) + VIGENCIA[subtipo]


def vigente(raw: Any, timestamp: datetime, ahora: datetime) -> bool:
    """¿Se muestra todavía? Ni cerrado por el MTT ni pasado de su vigencia."""
    mtt = bloque_mtt(raw)
    if mtt is None or "_mtt_fin" in raw:
        return False
    return vence(mtt["tipo"], timestamp) > _aware(ahora)


def tramo_texto(mtt: dict[str, Any]) -> str | None:
    """«Viana, de Sucre a Von Schroeders», «Av. España, altura Caleta Abarca»."""
    via = mtt.get("via")
    if not via:
        return None
    desde, hasta, punto = mtt.get("desde"), mtt.get("hasta"), mtt.get("punto")
    if desde and hasta:
        return f"{via}, de {desde} a {hasta}"
    if punto:
        return f"{via} con {punto}" if mtt.get("tipo") == "semaforo" else f"{via}, {punto}"
    return str(via)


def titulo(mtt: dict[str, Any]) -> str:
    """«Congestión en Viana, de Sucre a Von Schroeders», «Marcha por Av. Pedro Montt»."""
    nombre = NOMBRE.get(mtt.get("tipo", ""), "Tránsito")
    tramo = tramo_texto(mtt)
    if not tramo:
        return nombre
    preposicion = "por" if mtt.get("tipo") == "marcha" else "en"
    return f"{nombre} {preposicion} {tramo}"


def avisa(mtt: dict[str, Any] | None) -> bool:
    """¿Este aviso del MTT merece un push (a quien encendió «Tránsito»)?"""
    if mtt is None or mtt.get("tipo") not in CON_AVISO:
        return False
    if mtt.get("tipo") == "congestion":
        metodo = (mtt.get("ubicacion") or {}).get("metodo")
        return metodo in METODOS_CON_TRAMO
    return True


__all__ = [
    "CON_AVISO",
    "NOMBRE",
    "VIGENCIA",
    "avisa",
    "bloque_mtt",
    "titulo",
    "tramo_texto",
    "vence",
    "vigente",
]
