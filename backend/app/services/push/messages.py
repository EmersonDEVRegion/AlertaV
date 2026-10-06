"""Redacción de las notificaciones. Puro: entra un hecho, sale un mensaje.

Una notificación se lee de reojo, en la pantalla bloqueada, a veces a las tres
de la mañana. Tres reglas ordenan todo lo de acá:

1. **El título dice qué y a qué distancia.** Es lo único que se ve seguro en
   todos los teléfonos: «Incendio forestal a 1,2 km».
2. **El cuerpo dice dónde, desde cuándo y cuánto sabemos.** Y lo dice con las
   mismas palabras que la ficha del mapa: un aviso que afirma más que el mapa
   es una mentira con ícono.
3. **Nunca se afirma una verificación que no hubo.** Un incidente en el tramo
   `confirmed` sin CONAF ni Bomberos lleva «sin verificar en terreno», igual
   que el pin hueco del mapa.

La distancia se mide desde la última ubicación que el teléfono informó, no
desde donde está ahora (con la app cerrada, el navegador no la entrega). Por eso
el título dice «a 1,2 km» y no «a 1,2 km de ti»: la PWA explica el matiz en el
panel de avisos, y el título no promete una precisión que no tiene.
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
PAYLOAD_VERSION = 1

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

_ALERT_TEXT = {
    "roja": "Alerta roja de SENAPRED",
    "amarilla": "Alerta amarilla de SENAPRED",
    "temprana_preventiva": "Alerta temprana preventiva de SENAPRED",
}

_PROVIDER_NAME = {"csn": "CSN", "usgs": "USGS"}


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

    def payload(self, *, now: datetime) -> dict[str, Any]:
        """Lo que recibe `push-sw.js`. Debe caber holgado en ~3,9 KB."""
        return {
            "v": PAYLOAD_VERSION,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "tag": self.tag,
            "url": self.url,
            "ts": int(now.timestamp() * 1000),
        }

    @property
    def topic(self) -> str:
        return safe_topic(self.tag)


@dataclass(frozen=True, slots=True)
class OutageFacts:
    provider: str | None = None
    affected_clients: int | None = None
    estimated_restoration: datetime | None = None


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
        return "Confirmado por una fuente oficial"

    tramo = level_for(confidence)
    level = style_for(tramo, family_of_incident(incident_type))
    pct = round(confidence * 100)
    fuentes = "1 fuente" if source_count == 1 else f"{source_count} fuentes"
    # La etiqueta del tramo `confirmed` dice «Incendio confirmado». Sin una
    # fuente que haya ido al lugar eso es falso en una notificación, igual que lo
    # sería en el mapa sin el pin hueco; se reemplaza por lo que sí se sabe.
    if tramo is ConfidenceLevel.CONFIRMED:
        return f"Sin verificar en terreno · {fuentes} coinciden ({pct} %)"
    return f"{level.label} · {fuentes} ({pct} %)"


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

    Con `place` la distancia se midió desde un lugar guardado y el título lo
    nombra: «Incendio forestal a 1,2 km de Casa». Sin él, se midió desde la
    última ubicación del teléfono; si ésta tiene más de un día, el cuerpo lo
    dice, porque «a 1,2 km» de donde uno estuvo el lunes no es lo mismo.
    """
    noun = _NOUN.get(incident_type, "Emergencia")
    title = f"{noun} a {format_distance(distance_m)}"
    if place:
        title += f" de {place}"

    lines: list[str] = []

    where = [commune] if commune else []
    hora = format_clock(first_seen_at)
    # «desde la 01:15» pero «desde las 14:32»: la una es singular.
    articulo = "la" if hora.startswith("01:") else "las"
    where.append(f"desde {articulo} {hora}")
    lines.append(" · ".join(where))

    if incident_type is IncidentType.POWER_OUTAGE and outage is not None:
        detalle: list[str] = []
        if outage.provider:
            detalle.append(_VERIFIERS.get(outage.provider.lower(), outage.provider))
        if outage.affected_clients:
            n = outage.affected_clients
            detalle.append("1 cliente sin luz" if n == 1 else f"{_thousands(n)} clientes sin luz")
        if detalle:
            lines.append(" · ".join(detalle))
        if outage.estimated_restoration is not None:
            lines.append(f"Reposición estimada: {format_clock(outage.estimated_restoration)}")
    else:
        lines.append(
            _certainty(
                confidence=confidence,
                source_count=source_count,
                sources=sources,
                is_official_confirmed=is_official_confirmed,
                incident_type=incident_type,
            )
        )

    if alert_level in _ALERT_TEXT:
        lines.append(_ALERT_TEXT[alert_level])

    if not place and located_at is not None and now is not None:
        age = format_location_age(located_at, now)
        if age:
            lines.append(f"Distancia desde tu ubicación {age}")

    return PushMessage(
        title=title,
        body="\n".join(lines),
        tag=code,
        url="/?" + urlencode({"incidente": code}),
        kind="incident",
        ttl_seconds=INCIDENT_TTL_SECONDS,
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
) -> PushMessage:
    """Aviso de un sismo que probablemente se sintió en la ubicación."""
    title = f"Sismo de magnitud {_decimal(magnitude, 1)} a {format_distance(distance_m)}"

    first = [f"Epicentro a {format_distance(distance_m)} de tu ubicación"]
    first.append(format_clock(timestamp))
    lines = [" · ".join(first)]

    second: list[str] = []
    # La referencia del USGS viene en inglés («25 km WSW of Valparaíso»). Se
    # muestra igual: es la única que hay cuando el CSN todavía no publica, y la
    # fuente va nombrada al final para que se entienda el idioma.
    if place:
        second.append(place)
    if depth_km is not None:
        second.append(f"{round(abs(depth_km))} km de profundidad")
    if second:
        lines.append(" · ".join(second))
    lines.append(f"Fuente: {_PROVIDER_NAME.get(provider, provider.upper())}")

    return PushMessage(
        title=title,
        body="\n".join(lines),
        tag=f"sismo-{key}",
        url="/?" + urlencode({"sismo": key, "lat": f"{lat:.4f}", "lon": f"{lon:.4f}"}),
        kind="seismic",
        ttl_seconds=SEISMIC_TTL_SECONDS,
    )


WATER_CUT_TTL_SECONDS = 6 * 3600


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
    now: datetime | None = None,
) -> PushMessage:
    """Aviso de un corte de agua de Esval cerca de la ubicación o de un lugar guardado."""
    nombre = "Corte de agua programado" if programado else "Corte de agua"
    title = f"{nombre} a {format_distance(distance_m)}"
    if place:
        title += f" de {place}"

    lines: list[str] = []
    donde = [parte for parte in (calles or sector, comuna) if parte]
    if donde:
        # Las calles de Esval pueden ser un párrafo: el aviso lleva el comienzo.
        primera = donde[0] if len(donde[0]) <= 90 else donde[0][:87].rstrip() + "…"
        lines.append(" · ".join([primera, *donde[1:]]))
    desde, hasta = _momento(inicio, now), _momento(fin, now)
    if desde and hasta:
        lines.append(f"Desde {desde} hasta {hasta} (estimado)")
    elif desde:
        lines.append(f"Desde {desde}")
    elif hasta:
        lines.append(f"Reposición estimada: {hasta}")
    if motivo:
        lines.append(motivo)
    lines.append("Fuente: Esval")

    return PushMessage(
        title=title,
        body="\n".join(lines),
        tag=f"agua-{key}",
        url="/?" + urlencode({"corte_agua": public_id, "lat": f"{lat:.4f}", "lon": f"{lon:.4f}"}),
        kind="water_cut",
        ttl_seconds=WATER_CUT_TTL_SECONDS,
    )


_PROBE_NOMBRES = (
    ("fire", "incendios"),
    ("traffic", "accidentes"),
    ("power", "cortes de luz"),
    ("water", "cortes de agua"),
)


def probe_message(
    *, radius_m: float = 5000.0, radios: Mapping[str, float] | None = None
) -> PushMessage:
    """Lo que llega al tocar «Enviar prueba» en la PWA.

    Con `radios` (§K) cuenta las distancias que la persona eligió para las
    categorías principales; sin ellos, el radio único de antes.
    """
    if radios is None:
        cuando = f"una emergencia a menos de {format_distance(radius_m)}"
    else:
        partes = [
            f"{nombre} a menos de {format_distance(radios[clave])}"
            for clave, nombre in _PROBE_NOMBRES
            if radios.get(clave, 0) > 0
        ]
        cuando = (
            "una emergencia cerca (" + ", ".join(partes) + ")"
            if partes
            else "una emergencia cerca"
        )
    return PushMessage(
        title="AlertaV: avisos activados",
        body=f"Así llegará un aviso cuando haya {cuando} o un sismo que se sienta donde estás.",
        tag="alertav-prueba",
        url="/",
        kind="test",
        ttl_seconds=TEST_TTL_SECONDS,
    )

__all__ = [
    "INCIDENT_TTL_SECONDS",
    "SEISMIC_TTL_SECONDS",
    "OutageFacts",
    "PushMessage",
    "format_clock",
    "format_distance",
    "format_location_age",
    "incident_message",
    "probe_message",
    "safe_topic",
    "seismic_message",
    "water_cut_message",
]
