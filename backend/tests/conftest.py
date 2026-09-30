"""Ajustes comunes a toda la suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _sin_cache_de_respuestas() -> Iterator[None]:
    """La caché de lecturas (`app/core/cache_respuestas.py`) vive en el proceso.

    Encendida, un test que pide `/incidents/active` con un servicio simulado
    recibiría la respuesta que otro test simuló antes. Sus propios tests la
    encienden a mano.

    Sin `monkeypatch` a propósito: pedirlo acá lo instanciaría antes que los
    fixtures de cada archivo, y su deshacer correría después de ellos. Los que
    limpian algo que un test parchó (el cliente de Gemini, por ejemplo) se
    encontrarían todavía el parche.
    """
    anterior = settings.API_RESPONSE_CACHE_SECONDS
    settings.API_RESPONSE_CACHE_SECONDS = 0
    yield
    settings.API_RESPONSE_CACHE_SECONDS = anterior
