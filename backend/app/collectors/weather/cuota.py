"""Cuota de Open-Meteo compartida entre el clima por comuna y la grilla de lluvia.

El plan gratuito limita por IP: 600 llamadas por minuto, 5 000 por hora y
10 000 por día, y la salida de Render es compartida con otros servicios. Cuando
Open-Meteo responde 429 con un límite por hora o por día, insistir cada media
hora (comunas) o cada 15 minutos (grilla) no sirve: cada intento falla hasta que
la cuota se renueva. Este módulo recuerda hasta cuándo no vale la pena pedir.

El estado vive en memoria del proceso: en el modo `combined` de `start.sh` los
dos consumidores comparten proceso. Un reinicio lo olvida, y el primer intento
después vuelve a aprenderlo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.exceptions import CollectorError

_pausa_hasta: datetime | None = None


def renovacion(motivo: str | None, *, now: datetime) -> datetime | None:
    """Cuándo se renueva la cuota que agotó este 429. None si no hace falta esperar.

    Open-Meteo renueva el límite diario a las 00:00 UTC y el horario al comenzar
    la hora siguiente. El límite por minuto no se espera: el propio ciclo del
    collector ya es más largo.
    """
    texto = (motivo or "").lower()
    if "daily" in texto:
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if "hourly" in texto:
        return (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return None


def registrar(error: CollectorError, *, now: datetime | None = None) -> None:
    """Anota la pausa si `error` es un 429 por cuota horaria o diaria."""
    global _pausa_hasta
    detalle = error.detail if isinstance(error.detail, dict) else {}
    if detalle.get("status") != 429:
        return
    hasta = renovacion(detalle.get("motivo"), now=now or datetime.now(UTC))
    if hasta is not None:
        _pausa_hasta = hasta


def verificar(origin: str, *, now: datetime | None = None) -> None:
    """Sube `CollectorError` sin tocar la red si la cuota sigue agotada."""
    momento = now or datetime.now(UTC)
    if _pausa_hasta is not None and momento < _pausa_hasta:
        raise CollectorError(
            f"{origin}: cuota de Open-Meteo agotada; se vuelve a pedir desde las "
            f"{_pausa_hasta:%H:%M} UTC",
            detail={"pausa_hasta": _pausa_hasta.isoformat()},
        )


def reiniciar() -> None:
    """Olvida la pausa. Para los tests."""
    global _pausa_hasta
    _pausa_hasta = None
