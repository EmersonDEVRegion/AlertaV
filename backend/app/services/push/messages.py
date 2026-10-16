"""Redacción de las notificaciones. Puro: entra un hecho, sale un mensaje.

Una notificación se lee de reojo, en la pantalla bloqueada, a veces a las tres
de la mañana. Android muestra plegadas una o dos líneas, e iOS unas cuatro, y
además agrega su propia línea «from AlertaV». Cuatro reglas ordenan lo de acá
(§N, 2026-10-16):

1. **El título dice qué, a qué distancia y desde dónde.** Un emoji por tipo
   para leerlo sin leer: «🔥 Incendio forestal a 1,2 km de Casa». El origen va
   siempre: el nombre del lugar guardado, o «de tu última ubicación», porque
   con la app cerrada el navegador no entrega la ubicación actual y el
   servidor sólo conoce la última que el teléfono informó.
2. **El cuerpo es una línea:** dónde, desde cuándo y cuánto sabemos, separado
   por «·». Lo crítico (una alerta de SENAPRED) va antes, en su propia línea.
3. **Sin relleno:** nada de «Fuente:», porcentajes ni «(estimado)». La ficha del
   mapa tiene el detalle; el aviso sólo tiene que decir si vale la pena abrirla.
4. **Nunca se afirma una verificación que no hubo.** Un incidente en el tramo
   `confirmed` sin CONAF ni Bomberos dice «Sin verificar», igual que el pin
   hueco del mapa.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from app.models.enums import (
    ConfidenceLevel,
    IncidentType,
    family_of_incident,
    level_for,
    style_for,
)

CHILE_TZ = ZoneInfo("America/Santiago")

#: Versión del formato del mensaje. El service worker la lee para poder
#: convivir con mensajes viejos que sigan en cola tras un despliegue.
PAYLOAD_VERSION = 2

#: Cuánto guarda el servicio de push el mensaje si el teléfono no tiene señal.
#: Un incendio sigue siendo noticia una hora después; un sismo, a la media hora,
#: ya lo sintió todo el mundo y el aviso sólo estorba.
INCIDENT_TTL_SECONDS = 3600
SEISMIC_TTL_SECONDS = 1800
TEST_TTL_SECONDS = 300

#: Sustantivo para el título, por tipo. No es `TYPE_LABEL` del mapa: en una
#: notificación «Corte de luz» se entiende al instante y «Corte de suministro»
#: obliga a pensar.
_NOUN: dict[IncidentType, str] = {
    IncidentType.POSSIBLE_FIRE: "Posible incendio",
    IncidentType.WILDFIRE: "Incendio forestal",
    IncidentType.STRUCTURAL_FIRE: "Incendio estructural",
    IncidentType.FLOOD: "Inundación",
    IncidentType.LANDSLIDE: "Derrumbe",
    IncidentType.ACCIDENT: "Accidente de tránsito",
    IncidentType.POWER_OUTAGE: "Corte de luz",
    IncidentType.RESCUE: "Rescate",
    IncidentType.OTHER: "Emergencia",
}

#: Quién «fue al lugar», por fuente. Sólo las confirmatorias.
_VERIFIERS = {
    "conaf": "CONAF",
    "bomberos": "Bomberos",
    "chilquinta": "Chilquinta",
    "cge": "CGE",
}

_PROVIDER_NAME = {"csn": "CSN", "usgs": "USGS"}

#: Un emoji por tipo, al comienzo del título. En la bandeja se reconoce el tipo
#: antes de leer la palabra. En la interfaz se usan íconos (Material Symbols);
#: una notificación sólo admite texto, y ahí el emoji es el ícono.
_EMOJI: dict[IncidentType, str] = {
    IncidentType.POSSIBLE_FIRE: "🔥",
    IncidentType.WILDFIRE: "🔥",
    IncidentType.STRUCTURAL_FIRE: "🔥",
    IncidentType.FLOOD: "🌊",
    IncidentType.LANDSLIDE: "⛰️",
    IncidentType.ACCIDENT: "🚗",
    IncidentType.POWER_OUTAGE: "⚡",
    IncidentType.RESCUE: "🆘",
    IncidentType.OTHER: "⚠️",
}
SEISMIC_EMOJI = "🌎"
WATER_EMOJI = "💧"

#: La alerta de SENAPRED con el color de la alerta: es lo más importante del
#: aviso y va en la primera línea del cuerpo.
_ALERT_LINE = {
    "roja": "🔴 Alerta roja de SENAPRED",
    "amarilla": "🟡 Alerta amarilla de SENAPRED",
    "temprana_preventiva": "🟢 Alerta temprana preventiva de SENAPRED",
}

#: Desde dónde se midió la distancia cuando no fue desde un lugar guardado.
LAST_LOCATION = "tu última ubicación"


# ---------------------------------------------------------------------------
#  Formato
# ---------------------------------------------------------------------------


def _decimal(value: float, digits: int) -> str:
    """Número con coma decimal, como se escribe en Chile."""
    return f"{value:.{digits}f}".replace(".", ",")


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def format_distance(meters: float) -> str:
    """350 m · 1,2 km · 48 km.

    Bajo el kilómetro se redondea a 50 m: la ubicación se guarda redondeada a
    ~110 m y el incidente es un centroide, así que decir «a 347 m» sería
    inventar una precisión que no existe.
    """
    rounded = max(50, round(meters / 50) * 50)
    if rounded < 1000:
        return f"{rounded} m"
    km = round(meters / 100) / 10
    if km < 10:
        # «5 km» y no «5,0 km»: el decimal cero es ruido en una pantalla chica.
        return f"{_decimal(km, 1).removesuffix(',0')} km"
    return f"{round(km)} km"


#: Desde cuándo vale la pena decir que la ubicación es vieja. Menos de un día
#: suele ser la casa o el trabajo de hoy; más, puede ser otra ciudad.
STALE_LOCATION = timedelta(hours=24)


def format_location_age(located_at: datetime, now: datetime) -> str | None:
    """«de hace 3 días», o `None` si la ubicación es de las últimas 24 h."""
    age = now - located_at
    if age < STALE_LOCATION:
        return None
    days = age.days
    if days >= 30:
        return "de hace más de un mes"
    return "de hace 1 día" if days <= 1 else f"de hace {days} días"


def format_clock(moment: datetime) -> str:
    """Hora local de Chile, HH:MM."""
    return moment.astimezone(CHILE_TZ).strftime("%H:%M")


def safe_topic(value: str) -> str:
    """`Topic` válido: alfabeto base64url y a lo más 32 caracteres (RFC 8030)."""
    return re.sub(r"[^A-Za-z0-9_-]", "-", value)[:32]


# ---------------------------------------------------------------------------
#  Mensajes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PushMessage:
    title: str
    body: str
    tag: str
    url: str
    kind: str
    ttl_seconds: int
    #: Desde dónde se midió la distancia: `"place"` (un lugar guardado),
    #: `"location"` (la última ubicación del teléfono) o `None` (sin distancia,
    #: como el aviso de prueba).
    origin: str | None = None
    #: El nombre del lugar guardado, si `origin` es `"place"`.
    place: str | None = None

    def payload(self, *, now: datetime) -> dict[str, Any]:
        """Lo que recibe `push-sw.js`. Debe caber holgado en ~3,9 KB."""
        data: dict[str, Any] = {
            "v": PAYLOAD_VERSION,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "tag": self.tag,
            "url": self.url,
            "ts": int(now.timestamp() * 1000),
        }
        if self.origin:
            data["origin"] = self.origin
        if self.place:
            data["place"] = self.place
        return data

    @property
    def topic(self) -> str:
        return safe_topic(self.tag)


@dataclass(frozen=True, slots=True)
class OutageFacts:
    provider: str | None = None
    affected_clients: int | None = None
    estimated_restoration: datetime | None = None


@dataclass(frozen=True, slots=True)
class Origin:
    """Desde dónde se midió la distancia, ya redactado para el aviso."""

    #: «de Casa» o «de tu última ubicación», para el final del título.
    suffix: str
    #: «Ubicación de hace 3 días», o `None` si no hace falta decirlo.
    note: str | None
    kind: str
    place: str | None


def origin_of(*, place: str | None, located_at: datetime | None, now: datetime | None) -> Origin:
    """Redacta el origen de la distancia.

    Con `place` se midió desde un lugar guardado y se nombra. Sin él, se midió
    desde la última ubicación que el teléfono informó; si tiene más de un día,
    el cuerpo lo dice, porque «a 1,2 km» de donde uno estuvo el lunes no es lo
    mismo que de donde está.
    """
    if place:
        return Origin(suffix=f"de {place}", note=None, kind="place", place=place)
    note = None
    if located_at is not None and now is not None:
        age = format_location_age(located_at, now)
        if age:
            note = f"📍 Ubicación {age}"
    return Origin(suffix=f"de {LAST_LOCATION}", note=note, kind="location", place=None)


def _certainty(
    *,
    confidence: float,
    source_count: int,
    sources: Sequence[str],
    is_official_confirmed: bool,
    incident_type: IncidentType,
) -> str:
    if is_official_confirmed:
        verifiers = [name for key, name in _VERIFIERS.items() if key in sources]
        if verifiers:
            quien = " y ".join(verifiers) if len(verifiers) <= 2 else ", ".join(verifiers)
            verbo = "Informado" if incident_type is IncidentType.POWER_OUTAGE else "Confirmado"
            return f"{verbo} por {quien}"
        return "Confirmado por fuente oficial"

    tramo = level_for(confidence)
    level = style_for(tramo, family_of_incident(incident_type))
    fuentes = "1 fuente" if source_count == 1 else f"{source_count} fuentes"
    # La etiqueta del tramo `confirmed` dice «Incendio confirmado». Sin una
    # fuente que haya ido al lugar eso es falso en una notificación, igual que lo
    # sería en el mapa sin el pin hueco; se reemplaza por lo que sí se sabe.
    if tramo is ConfidenceLevel.CONFIRMED:
        return f"Sin verificar ({fuentes})"
    return f"{level.label} ({fuentes})"


def _join(parts: Sequence[str | None]) -> str:
    return " · ".join(part for part in parts if part)


def incident_message(
    *,
    code: str,
    incident_type: IncidentType,
    distance_m: float,
    commune: str | None,
    first_seen_at: datetime,
    confidence: float,
    source_count: int,
    sources: Sequence[str],
    is_official_confirmed: bool,
    alert_level: str | None,
    outage: OutageFacts | None = None,
    place: str | None = None,
    located_at: datetime | None = None,
    now: datetime | None = None,
) -> PushMessage:
    """Aviso de una emergencia cercana.

    🔥 Incendio forestal a 1,2 km de Casa
    Viña del Mar · desde 14:32 · Confirmado por Bomberos
    """
    origin = origin_of(place=place, located_at=located_at, now=now)
    noun = _NOUN.get(incident_type, "Emergencia")
    emoji = _EMOJI.get(incident_type, "⚠️")
    title = f"{emoji} {noun} a {format_distance(distance_m)} {origin.suffix}"

    lines: list[str] = []
    if alert_level in _ALERT_LINE:
        lines.append(_ALERT_LINE[alert_level])

    desde = f"desde {format_clock(first_seen_at)}"
    if incident_type is IncidentType.POWER_OUTAGE and outage is not None:
        clientes = None
        if outage.affected_clients:
            n = outage.affected_clients
            clientes = "1 cliente" if n == 1 else f"{_thousands(n)} clientes"
        vuelve = (
            f"vuelve ~{format_clock(outage.estimated_restoration)}"
            if outage.estimated_restoration is not None
            else None
        )
        proveedor = (
            _VERIFIERS.get(outage.provider.lower(), outage.provider) if outage.provider else None
        )
        lines.append(_join([commune, desde, clientes, vuelve, proveedor]))
    else:
        lines.append(
            _join(
                [
                    commune,
                    desde,
                    _certainty(
                        confidence=confidence,
                        source_count=source_count,
                        sources=sources,
                        is_official_confirmed=is_official_confirmed,
                        incident_type=incident_type,
                    ),
                ]
            )
        )

    if origin.note:
        lines.append(origin.note)

    return PushMessage(
        title=title,
        body="\n".join(lines),
        tag=code,
        url="/?" + urlencode({"incidente": code}),
        kind="incident",
        ttl_seconds=INCIDENT_TTL_SECONDS,
        origin=origin.kind,
        place=origin.place,
    )


def seismic_message(
    *,
    key: str,
    provider: str,
    magnitude: float,
    distance_m: float,
    timestamp: datetime,
    lat: float,
    lon: float,
    place: str | None,
    depth_km: float | None,
    saved_place: str | None = None,
    located_at: datetime | None = None,
    now: datetime | None = None,
) -> PushMessage:
    """Aviso de un sismo que probablemente se sintió en la ubicación o en un lugar.

        🌎 Sismo 4,8 a 62 km de tu última ubicación
        25 km al O de Valparaíso · 14:32 · 35 km de prof. · CSN

    `place` es la referencia del epicentro que publica la red; `saved_place`, el
    lugar guardado desde el que se midió la distancia.
    """
    origin = origin_of(place=saved_place, located_at=located_at, now=now)
    title = (
        f"{SEISMIC_EMOJI} Sismo {_decimal(magnitude, 1)} "
        f"a {format_distance(distance_m)} {origin.suffix}"
    )

    # La referencia del USGS viene en inglés («25 km WSW of Valparaíso»). Se
    # muestra igual: es la única que hay cuando el CSN todavía no publica, y la
    # red va nombrada al final para que se entienda el idioma.
    profundidad = f"{round(abs(depth_km))} km de prof." if depth_km is not None else None
    red = _PROVIDER_NAME.get(provider, provider.upper())
    lines = [_join([place, format_clock(timestamp), profundidad, red])]
    if origin.note:
        lines.append(origin.note)

    return PushMessage(
        title=title,
        body="\n".join(lines),
        tag=f"sismo-{key}",
        url="/?" + urlencode({"sismo": key, "lat": f"{lat:.4f}", "lon": f"{lon:.4f}"}),
        kind="seismic",
        ttl_seconds=SEISMIC_TTL_SECONDS,
        origin=origin.kind,
        place=origin.place,
    )


WATER_CUT_TTL_SECONDS = 6 * 3600

#: Largo máximo de las calles en el aviso. Esval puede mandar un párrafo.
_CALLES_MAX = 60


def _momento(valor: str | None, now: datetime | None) -> str | None:
    """«14:30» si es hoy en Chile; «06/10 08:00» si es otro día. None si no se lee."""
    if not valor:
        return None
    try:
        momento = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=CHILE_TZ)
    local = momento.astimezone(CHILE_TZ)
    hoy = (now or datetime.now(CHILE_TZ)).astimezone(CHILE_TZ).date()
    if local.date() == hoy:
        return local.strftime("%H:%M")
    return local.strftime("%d/%m %H:%M")


def water_cut_message(
    *,
    key: str,
    public_id: str,
    lat: float,
    lon: float,
    distance_m: float,
    comuna: str | None,
    calles: str | None,
    sector: str | None,
    inicio: str | None,
    fin: str | None,
    programado: bool | None,
    motivo: str | None,
    place: str | None = None,
    located_at: datetime | None = None,
    now: datetime | None = None,
) -> PushMessage:
    """Aviso de un corte de agua de Esval cerca de la ubicación o de un lugar guardado.

        💧 Corte de agua programado a 650 m de Casa
        1 Norte entre 3 y 5 Poniente · Viña del Mar · 23:00 a 06/10 06:00 · Esval

    El motivo no va: casi siempre es «mantención» y no cambia qué hacer.
    """
    del motivo  # Ver el docstring: se recibe para no cambiar la firma del notificador.
    origin = origin_of(place=place, located_at=located_at, now=now)
    nombre = "Corte de agua programado" if programado else "Corte de agua"
    title = f"{WATER_EMOJI} {nombre} a {format_distance(distance_m)} {origin.suffix}"

    donde = calles or sector
    if donde and len(donde) > _CALLES_MAX:
        donde = donde[: _CALLES_MAX - 1].rstrip(" ,;") + "…"
    desde, hasta = _momento(inicio, now), _momento(fin, now)
    if desde and hasta:
        cuando: str | None = f"{desde} a {hasta}"
    elif desde:
        cuando = f"desde {desde}"
    elif hasta:
        cuando = f"hasta {hasta}"
    else:
        cuando = None

    lines = [_join([donde, comuna, cuando, "Esval"])]
    if origin.note:
        lines.append(origin.note)

    return PushMessage(
        title=title,
        body="\n".join(lines),
        tag=f"agua-{key}",
        url="/?" + urlencode({"corte_agua": public_id, "lat": f"{lat:.4f}", "lon": f"{lon:.4f}"}),
        kind="water_cut",
        ttl_seconds=WATER_CUT_TTL_SECONDS,
        origin=origin.kind,
        place=origin.place,
    )


_PROBE_CATEGORIAS = (
    ("fire", "🔥"),
    ("traffic", "🚗"),
    ("power", "⚡"),
    ("water", "💧"),
)


def probe_message(
    *, radius_m: float = 5000.0, radios: Mapping[str, float] | None = None
) -> PushMessage:
    """Lo que llega al tocar «Enviar prueba» en la PWA.

        ✅ Avisos activados
        🔥 5 km · 🚗 2 km · ⚡ 1 km · 💧 1 km · 🌎 sismos que se sientan

    El título no dice «AlertaV»: el teléfono ya agrega el nombre de la app.
    """
    if radios is None:
        partes = [f"Emergencias a menos de {format_distance(radius_m)}"]
    else:
        partes = [
            f"{emoji} {format_distance(radios[clave])}"
            for clave, emoji in _PROBE_CATEGORIAS
            if radios.get(clave, 0) > 0
        ]
    partes.append(f"{SEISMIC_EMOJI} sismos que se sientan")
    return PushMessage(
        title="✅ Avisos activados",
        body=" · ".join(partes) + f"\nDistancias desde {LAST_LOCATION} y tus lugares guardados.",
        tag="alertav-prueba",
        url="/",
        kind="test",
        ttl_seconds=TEST_TTL_SECONDS,
    )


__all__ = [
    "INCIDENT_TTL_SECONDS",
    "LAST_LOCATION",
    "SEISMIC_TTL_SECONDS",
    "Origin",
    "OutageFacts",
    "PushMessage",
    "format_clock",
    "format_distance",
    "format_location_age",
    "incident_message",
    "origin_of",
    "probe_message",
    "safe_topic",
    "seismic_message",
    "water_cut_message",
]
