"""Texto libre → punto: el geocodificador LLM que comparten varios caminos.

Vivía en el worker de Instagram, que salió del repositorio el 2026-09-23. Lo
siguen usando `services/backfill.py` (rescate de eventos sin coordenadas) y
los tests de sectores, así que se muda acá en vez de borrarse con él.
"""

from __future__ import annotations

from typing import Any

from app.collectors.lugares import anotar_sector
from app.collectors.nominatim import GeocodeResult, geocode
from app.collectors.traffic.transporteinforma_worker import extract_streets_via_llm


async def geocode_text(
    text: str, *, geo_client: Any
) -> tuple[dict[str, Any], GeocodeResult | None]:
    """Texto libre → `({street_1, street_2, city, sector}, punto|None)`.

    Es un adaptador de dos pasos sobre el pipeline que ya existe:

    * `extract_streets_via_llm` (en `traffic/transporteinforma_worker`) llama a
      Gemini y cae a la heurística de reglas si el modelo no está configurado o
      no resuelve.
    * `nominatim.geocode` convierte ese diccionario en un punto, respetando el
      límite de 1 req/s que Nominatim impone por IP.

    Devuelve las **dos** piezas y no sólo lat/lon: cuando un punto esté mal, la
    pregunta va a ser cuál de los dos pasos falló. Van separadas a
    `raw_data._extraction` y `raw_data._geocoding`.

    No lanza por fallos del modelo —`extract_streets_via_llm` ya los absorbe—
    pero **sí** deja pasar los de Nominatim: quien llama decide si un fallo de
    geocodificación vale una degradación de la corrida.
    """
    streets = await extract_streets_via_llm(text)
    if not streets or not streets.get("street_1"):
        streets = {}

    # Un texto sin calle pero con sector ("… en el sector de Miraflores Alto")
    # no se descarta: el sector se geocodifica solo si no hay vía, y su clave es
    # lo que deja al motor reconocer el mismo hecho contado por otra fuente.
    streets = anotar_sector(streets, text)
    if not streets.get("street_1") and not streets.get("sector"):
        return ({}, None)

    point = await geocode(geo_client, streets)
    return (streets, point)


__all__ = ["geocode_text"]
