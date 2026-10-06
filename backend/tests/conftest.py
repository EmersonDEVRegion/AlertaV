"""Ajustes comunes a toda la suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _sin_cache_ni_red_opcional() -> Iterator[None]:
    """Apaga lo que vive en el proceso o sale a la red sin que el test lo pida.

    * La caché de lecturas (`app/core/cache_respuestas.py`): encendida, un test
      que pide `/incidents/active` con un servicio simulado recibiría la
      respuesta que otro test simuló antes.
    * Overpass (`app/collectors/overpass.py`): cada geocodificación con dos
      calles saldría a buscar el cruce a un servidor público.

    Sus propios tests los encienden a mano.

    Sin `monkeypatch` a propósito: pedirlo acá lo instanciaría antes que los
    fixtures de cada archivo, y su deshacer correría después de ellos. Los que
    limpian algo que un test parchó (el cliente de Gemini, por ejemplo) se
    encontrarían todavía el parche.
    """
    anteriores = (settings.API_RESPONSE_CACHE_SECONDS, settings.OVERPASS_ENABLED)
    settings.API_RESPONSE_CACHE_SECONDS = 0
    settings.OVERPASS_ENABLED = False
    yield
    settings.API_RESPONSE_CACHE_SECONDS, settings.OVERPASS_ENABLED = anteriores
