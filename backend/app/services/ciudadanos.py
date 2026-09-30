"""Reglas del reporte ciudadano que no dependen de la base ni de la red.

Acá viven, juntas y sin E/S, las decisiones de §C (2026-09-30):

* **Quién reporta.** Cada reporte guarda en `raw_data._ciudadano` una huella de
  su dispositivo y otra de su red, nunca los valores: un HMAC con sal. Alcanzan
  para saber si dos reportes vienen de la misma persona o del mismo edificio y
  no sirven para saber quién es nadie.
* **Qué es independiente.** Dos reportes cuentan como dos vecinos sólo si
  vienen de dispositivos distintos **y** de redes distintas. Lo primero frena a
  quien reporta dos veces; lo segundo, a quien rota el identificador del
  navegador sin cambiar de wifi. Ver `seleccionar_independientes`.
* **Qué texto se publica.** Ninguno hasta que alguien —Gemini o un operador— lo
  apruebe. `texto_publico` es la única puerta.
* **Qué se rechaza de entrada.** Puntos fuera de la región y textos con datos
  personales evidentes (teléfonos, correos, RUT, enlaces). Ver `prefiltro`.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from app.core.config import settings
from app.models.enums import EventSource

#: Clave de `raw_data` con las huellas de quien reporta.
CLAVE_CIUDADANO = "_ciudadano"
#: Clave de `raw_data` con el estado de moderación del texto.
CLAVE_MODERACION = "_moderacion"

PENDIENTE = "pendiente"
APROBADO = "aprobado"
RECHAZADO = "rechazado"
ESTADOS_MODERACION = (PENDIENTE, APROBADO, RECHAZADO)

#: Sal de último recurso, sólo fuera de producción. Con ella las huellas siguen
#: siendo estables entre reinicios, que es lo que importa para contar vecinos.
_SAL_LOCAL = "alertav-local-sin-sal"


# --- Huellas ----------------------------------------------------------------


def _sal() -> bytes:
    sal = settings.CITIZEN_HASH_SALT.strip() or settings.OPERATOR_TOKEN.strip()
    return (sal or _SAL_LOCAL).encode("utf-8")


def huella(valor: str, *, tipo: str) -> str:
    """HMAC-SHA256 truncado a 20 hex (80 bits). `tipo` separa dominios.

    Con `tipo` distinto, el mismo texto da huellas distintas: una IP usada como
    dispositivo de respaldo no coincide nunca con la huella de su propia red.
    """
    mensaje = f"{tipo}:{valor}".encode()
    return hmac.new(_sal(), mensaje, hashlib.sha256).hexdigest()[:20]


def red_de(ip: str) -> str:
    """La red de una IP: /24 en IPv4, /48 en IPv6. La IP tal cual si no se lee.

    Un /24 es el edificio, la oficina o el bloque de un operador; un /48 es lo
    que un proveedor le asigna a un hogar en IPv6. Dos reportes de la misma red
    no cuentan como dos vecinos.
    """
    try:
        direccion = ipaddress.ip_address(ip.strip())
    except ValueError:
        return ip.strip() or "desconocida"
    prefijo = 24 if direccion.version == 4 else 48
    return str(ipaddress.ip_network(f"{direccion}/{prefijo}", strict=False))


def huellas_de(*, ip: str, dispositivo: str | None) -> dict[str, str]:
    """Lo que se guarda en `raw_data._ciudadano`.

    Sin identificador de dispositivo (un cliente viejo) se usa la IP: esa persona
    sigue contando una vez, y su red sigue contando aparte.
    """
    base = dispositivo.strip() if dispositivo and dispositivo.strip() else f"ip:{ip}"
    return {
        "dispositivo": huella(base, tipo="dispositivo"),
        "red": huella(red_de(ip), tipo="red"),
    }


# --- Independencia ------------------------------------------------------------


def _huellas_guardadas(senal: Any) -> tuple[str, str]:
    """Dispositivo y red de un reporte. Uno sin huellas es su propio vecino."""
    datos = (senal.raw_data or {}).get(CLAVE_CIUDADANO) or {}
    propio = f"evento:{senal.id}"
    dispositivo = datos.get("dispositivo") if isinstance(datos, Mapping) else None
    red = datos.get("red") if isinstance(datos, Mapping) else None
    return (str(dispositivo or propio), str(red or propio))


#: Una señal: `RawEvent` o un doble de prueba con `id`, `source`, `timestamp` y
#: `raw_data`. Sin cota a propósito: con un `Protocol`, mypy ve los atributos de
#: `RawEvent` como `Mapped[...]` y no los reconoce.
S = TypeVar("S")


def seleccionar_independientes(senales: Sequence[S]) -> list[S]:
    """Los reportes ciudadanos que cuentan como vecinos distintos.

    Recorre por orden de llegada y acepta un reporte sólo si ni su dispositivo
    ni su red aparecieron antes. Es codicioso a propósito: el primero que llegó
    es el que se queda, y un reporte repetido no desplaza a nadie.

    Las señales de otras fuentes no pasan por acá; el llamador las conserva.
    """
    vistos_dispositivo: set[str] = set()
    vistas_red: set[str] = set()
    elegidos: list[S] = []
    for senal in sorted(senales, key=lambda s: (cast(Any, s).timestamp, cast(Any, s).id)):
        if cast(Any, senal).source is not EventSource.CITIZEN:
            continue
        dispositivo, red = _huellas_guardadas(senal)
        if dispositivo in vistos_dispositivo or red in vistas_red:
            continue
        vistos_dispositivo.add(dispositivo)
        vistas_red.add(red)
        elegidos.append(senal)
    return elegidos


def solo_ciudadano(fuentes: Iterable[EventSource | str]) -> bool:
    """¿Lo sostienen únicamente reportes ciudadanos (o nada)?"""
    valores = {f.value if isinstance(f, EventSource) else str(f) for f in fuentes}
    return valores <= {EventSource.CITIZEN.value}


def es_publico(
    *, fuentes: Iterable[EventSource | str], independientes: int, freno: bool
) -> bool:
    """¿Se muestra en el mapa?

    Todo lo que tenga una fuente no ciudadana, sí: eso no cambió. Lo sólo
    ciudadano, cuando junta `CITIZEN_QUORUM` vecinos independientes y el freno
    global no está puesto.
    """
    if not solo_ciudadano(fuentes):
        return True
    if freno:
        return False
    return independientes >= settings.CITIZEN_QUORUM


# --- Geocerca -----------------------------------------------------------------


def dentro_de_la_region(lat: float, lon: float) -> bool:
    """¿El punto cae en la caja de la V Región continental, con su margen?"""
    margen = settings.CITIZEN_GEOFENCE_MARGIN_DEG
    return (
        settings.REGION_SOUTH - margen <= lat <= settings.REGION_NORTH + margen
        and settings.REGION_WEST - margen <= lon <= settings.REGION_EAST + margen
    )


# --- Texto --------------------------------------------------------------------

_TELEFONO = re.compile(r"\+?\d(?:[\s.-]?\d){8,}")
_CORREO = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_RUT = re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-?[\dkK]\b")
_ENLACE = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|cl|net|org|io|ly|me)\b", re.I)
_ARROBA = re.compile(r"(?<![\w.])@\w{3,}")


def prefiltro(texto: str | None) -> str | None:
    """Motivo de rechazo inmediato, o None si el texto puede ir a revisión.

    Sólo lo que se detecta sin dudas y sin modelo: un número de teléfono, un
    correo, un RUT, un enlace o una mención a una cuenta. Nada de eso describe
    una emergencia y todo puede exponer a alguien. El resto lo juzga Gemini o un
    operador.
    """
    if not texto:
        return None
    if _CORREO.search(texto):
        return "correo electrónico"
    if _RUT.search(texto):
        return "RUT"
    if _TELEFONO.search(texto):
        return "número de teléfono"
    if _ENLACE.search(texto):
        return "enlace"
    if _ARROBA.search(texto):
        return "mención a una cuenta"
    return None


def moderacion_inicial(texto: str | None, *, ahora: datetime | None = None) -> dict[str, Any]:
    """El `_moderacion` con que nace un reporte."""
    ahora = ahora or datetime.now(UTC)
    motivo = prefiltro(texto)
    if motivo is not None:
        return {"estado": RECHAZADO, "por": "filtro", "motivo": motivo, "en": ahora.isoformat()}
    return {"estado": PENDIENTE, "por": None, "motivo": None, "en": ahora.isoformat()}


def estado_moderacion(raw_data: Mapping[str, Any] | None) -> str | None:
    datos = (raw_data or {}).get(CLAVE_MODERACION)
    if isinstance(datos, Mapping):
        estado = datos.get("estado")
        return str(estado) if estado in ESTADOS_MODERACION else None
    return None


def texto_publico(
    source: EventSource, raw_data: Mapping[str, Any] | None, texto: str | None
) -> tuple[str | None, bool]:
    """(texto a mostrar, ¿se ocultó?). La única puerta del texto ciudadano.

    Para cualquier otra fuente devuelve el texto tal cual. Para un ciudadano,
    sólo si la moderación lo aprobó; si no, `None` y `True`, para que la ficha
    pueda decir «comentario en revisión» en vez de callar.
    """
    if source is not EventSource.CITIZEN:
        return (texto, False)
    if not texto:
        return (None, False)
    if estado_moderacion(raw_data) == APROBADO:
        return (texto, False)
    return (None, True)


__all__ = [
    "APROBADO",
    "CLAVE_CIUDADANO",
    "CLAVE_MODERACION",
    "PENDIENTE",
    "RECHAZADO",
    "dentro_de_la_region",
    "es_publico",
    "estado_moderacion",
    "huella",
    "huellas_de",
    "moderacion_inicial",
    "prefiltro",
    "red_de",
    "seleccionar_independientes",
    "solo_ciudadano",
    "texto_publico",
]
