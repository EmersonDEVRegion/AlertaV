"""Enlace compartible de un incidente: `alertav.vercel.app/i/INC-2026-00142`.

WhatsApp, Telegram, X y compañía no ejecutan JavaScript: leen las etiquetas
Open Graph del HTML que reciben y con eso arman la vista previa. La PWA es una
página única que no sabe nada del incidente hasta que corre, así que la vista
previa la responde el backend.

Vercel reenvía `/i/:code` hasta acá (`frontend/vercel.json`). Lo que vuelve es
un HTML mínimo con las etiquetas y un `meta refresh` al deep link que ya usan
las notificaciones (`/?incidente=INC-…`): la persona que toca el enlace termina
en la app con la ficha abierta, y el robot que arma la vista previa se queda con
las etiquetas.

Sin scripts: la CSP de Vercel se aplica también a lo que se reenvía, y una
redirección por `meta refresh` no necesita ninguno.

La imagen es fija por tipo (`frontend/public/og/<tipo>.png`): no se generan
imágenes en el servidor del plan gratuito.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api.deps import IncidentServiceDep
from app.core.config import settings
from app.models.enums import IncidentStatus, IncidentType

router = APIRouter(prefix="/share", tags=["share"])

_CHILE = ZoneInfo("America/Santiago")

TYPE_LABEL: dict[IncidentType, str] = {
    IncidentType.POSSIBLE_FIRE: "Posible incendio",
    IncidentType.WILDFIRE: "Incendio forestal",
    IncidentType.STRUCTURAL_FIRE: "Incendio estructural",
    IncidentType.FLOOD: "Inundación",
    IncidentType.LANDSLIDE: "Remoción en masa",
    IncidentType.ACCIDENT: "Accidente",
    IncidentType.RESCUE: "Rescate",
    IncidentType.POWER_OUTAGE: "Corte de luz",
    IncidentType.OTHER: "Emergencia",
}

#: Imagen de la vista previa por tipo. Ver `frontend/scripts/og-images.mjs`.
TYPE_IMAGE: dict[IncidentType, str] = {
    IncidentType.POSSIBLE_FIRE: "incendio",
    IncidentType.WILDFIRE: "incendio",
    IncidentType.STRUCTURAL_FIRE: "incendio",
    IncidentType.FLOOD: "inundacion",
    IncidentType.LANDSLIDE: "inundacion",
    IncidentType.ACCIDENT: "accidente",
    IncidentType.RESCUE: "rescate",
    IncidentType.POWER_OUTAGE: "servicio",
    IncidentType.OTHER: "emergencia",
}

STATUS_LABEL: dict[IncidentStatus, str] = {
    IncidentStatus.ACTIVE: "En curso",
    IncidentStatus.CONTROLLED: "Controlado",
    IncidentStatus.EXTINGUISHED: "Terminado",
    IncidentStatus.STALE: "Sin novedades",
}


def _app_url() -> str:
    return settings.PUBLIC_APP_URL.rstrip("/")


def _hora(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    local = dt.astimezone(_CHILE)
    return local.strftime("%d-%m %H:%M")


def share_card(incident: Any | None, code: str) -> dict[str, str]:
    """Título, descripción, imagen y destino de la vista previa.

    Sin incidente (un folio que no existe o ya se fusionó en otro que no se
    encontró) la tarjeta es la genérica de AlertaV y el destino es el mapa: un
    enlace viejo nunca debe terminar en un error.
    """
    base = _app_url()
    if incident is None:
        return {
            "title": "AlertaV — Emergencias de la Región de Valparaíso",
            "description": (
                "Mapa de emergencias de la Quinta Región con fuentes oficiales, "
                "satélites y vecinos."
            ),
            "image": f"{base}/og/emergencia.png",
            "target": f"{base}/",
            "url": f"{base}/i/{quote(code)}",
        }

    kind = TYPE_LABEL.get(incident.type, "Emergencia")
    lugar = incident.commune or "Región de Valparaíso"
    titulo = f"{kind} — {lugar}"
    partes = [STATUS_LABEL.get(incident.status, "")]
    partes.append(f"desde {_hora(incident.first_seen_at)}")
    fuentes = incident.source_count
    partes.append(f"{fuentes} fuente{'s' if fuentes != 1 else ''}")
    if incident.is_official_confirmed:
        partes.append("confirmado por fuente oficial")
    descripcion = " · ".join(p for p in partes if p) + ". Ver en el mapa de AlertaV."

    return {
        "title": titulo,
        "description": descripcion,
        "image": f"{base}/og/{TYPE_IMAGE.get(incident.type, 'emergencia')}.png",
        "target": f"{base}/?incidente={quote(incident.code)}",
        "url": f"{base}/i/{quote(incident.code)}",
    }


def render_share_page(card: dict[str, str]) -> str:
    e = {k: escape(v, quote=True) for k, v in card.items()}
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e["title"]} · AlertaV</title>
<meta name="description" content="{e["description"]}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="AlertaV">
<meta property="og:locale" content="es_CL">
<meta property="og:title" content="{e["title"]}">
<meta property="og:description" content="{e["description"]}">
<meta property="og:url" content="{e["url"]}">
<meta property="og:image" content="{e["image"]}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="robots" content="noindex">
<meta http-equiv="refresh" content="0; url={e["target"]}">
<link rel="canonical" href="{e["url"]}">
</head>
<body style="font-family:system-ui,sans-serif;background:#0b0b0d;color:#eee;padding:24px">
<p><a href="{e["target"]}" style="color:#f97316">{e["title"]}</a></p>
</body>
</html>
"""


@router.get("/i/{code}", response_class=HTMLResponse, include_in_schema=False)
async def share_incident(code: str, service: IncidentServiceDep) -> HTMLResponse:
    detail = None
    normalized = code.strip().upper()
    if normalized.startswith("INC-") and len(normalized) <= 32:
        detail = await service.get_detail(code=normalized)
    card = share_card(detail, normalized)
    return HTMLResponse(
        render_share_page(card),
        headers={"Cache-Control": "public, max-age=300"},
    )
