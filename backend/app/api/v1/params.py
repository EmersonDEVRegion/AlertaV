"""Parseo de parámetros compartidos entre endpoints."""

from __future__ import annotations

from fastapi import HTTPException, status


def parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    """`west,south,east,north` en WGS84.

    Falla con 422 en vez de devolver `None` ante un valor malformado: un bbox
    ignorado en silencio devolvería la región entera, y el cliente creería que
    su filtro funcionó.
    """
    if not value:
        return None
    parts = value.split(",")
    if len(parts) != 4:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="bbox debe tener el formato west,south,east,north",
        )
    try:
        west, south, east, north = (float(part) for part in parts)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="bbox no numérico"
        ) from exc
    if west >= east or south >= north:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="bbox invertido"
        )
    return (west, south, east, north)
