"""La capa de amenaza sísmica, y lo que pasa cuando su artefacto no está.

Contexto
--------
La amenaza sísmica no es una señal del pipeline: es el modelo probabilístico
MASCSN26 del CSN, que se baja a mano con `scripts/fetch_seismic_hazard.py` y
queda como un GeoJSON estático. Antes se servía con el `StaticFiles` montado en
`/static`, y ese montaje tiene dos modos de fallo que sólo aparecen en
producción:

* el artefacto **no se ha generado** —es un paso manual del despliegue— y el
  servidor contesta un 404 desnudo que no dice qué hacer;
* el frontend vive en otro origen, así que una ruta relativa `/static/...` se
  resuelve contra el dominio del frontend, que nunca tuvo el archivo.

Estos tests fijan el contrato de la ruta que lo reemplaza: sirve el artefacto,
revalida con `ETag`, se apoya en la última copia buena cuando el disco falla y
—sólo cuando no le queda nada— responde **502 con un sobre JSON accionable**, no
con un volcado de pila.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_hazard_service
from app.main import app
from app.services.hazard_service import SeismicHazardService

RUTA = "/api/v1/events/seismic/hazard"


def _artefacto(features: int = 2) -> dict[str, Any]:
    """Un artefacto mínimo pero con la forma real que escribe el script."""
    return {
        "type": "FeatureCollection",
        "metadata": {
            "model": "MASCSN26",
            "generated_at": "2026-08-25T12:00:00+00:00",
            "cell_size_deg": {"lon": 0.045, "lat": 0.045},
        },
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0]]]},
                "properties": {"lon": -71.6 - i, "lat": -33.0, "pga_475": 0.4 + i},
            }
            for i in range(features)
        ],
    }


@pytest.fixture
def hazard(tmp_path: Path) -> Any:
    """Cliente apuntado a un artefacto de mentira, con la caché limpia.

    La caché del servicio es de clase —tiene que sobrevivir a la petición, que
    es cuando hace falta— así que se vacía en los dos extremos del test. Si no,
    el artefacto de un test se serviría como respaldo del siguiente y los casos
    de fallo pasarían por el motivo equivocado.
    """
    destino = tmp_path / "amenaza_sismica_valpo.json"
    SeismicHazardService.reset_cache()
    app.dependency_overrides[get_hazard_service] = lambda: SeismicHazardService(destino)
    yield TestClient(app), destino
    app.dependency_overrides.pop(get_hazard_service, None)
    SeismicHazardService.reset_cache()


def _escribir(destino: Path, payload: dict[str, Any]) -> None:
    destino.write_text(json.dumps(payload), encoding="utf-8")


class TestCaminoFeliz:
    def test_sirve_el_artefacto_con_etag(self, hazard: Any) -> None:
        client, destino = hazard
        _escribir(destino, _artefacto())

        response = client.get(RUTA)

        assert response.status_code == 200
        cuerpo = response.json()
        assert cuerpo["type"] == "FeatureCollection"
        assert len(cuerpo["features"]) == 2
        assert response.headers["ETag"]
        assert response.headers["X-AlertaV-Hazard-Stale"] == "false"
        assert response.headers["X-AlertaV-Hazard-Generated-At"].startswith("2026-08-25")

    def test_if_none_match_devuelve_304_sin_cuerpo(self, hazard: Any) -> None:
        """El modelo cambia cada varios años; el mapa se abre cada día.

        Sin revalidación condicional, cada carga se traería cientos de KB que ya
        están en el navegador.
        """
        client, destino = hazard
        _escribir(destino, _artefacto())

        etag = client.get(RUTA).headers["ETag"]
        response = client.get(RUTA, headers={"If-None-Match": etag})

        assert response.status_code == 304
        assert response.content == b""

    def test_relee_el_archivo_cuando_cambia(self, hazard: Any) -> None:
        """Regenerar el artefacto tiene que verse sin reiniciar el proceso."""
        client, destino = hazard
        _escribir(destino, _artefacto(features=2))
        primer_etag = client.get(RUTA).headers["ETag"]

        _escribir(destino, _artefacto(features=5))
        # `mtime` explícito: dos escrituras seguidas pueden compartir marca en
        # sistemas de archivos de baja resolución, y entonces el test mediría la
        # caché en vez del relectura.
        os.utime(destino, (1_800_000_000, 1_800_000_000))

        response = client.get(RUTA)

        assert len(response.json()["features"]) == 5
        assert response.headers["ETag"] != primer_etag


class TestRespaldo:
    """Lo que se sirve cuando el disco deja de colaborar."""

    def test_sirve_la_ultima_copia_buena_si_el_artefacto_desaparece(
        self, hazard: Any
    ) -> None:
        """Un despliegue parcial no puede apagar la capa.

        El dato es un modelo de hace meses: la copia en memoria es tan válida
        como la del disco. Lo que no puede pasar es servirla en silencio, y por
        eso va marcada.
        """
        client, destino = hazard
        _escribir(destino, _artefacto())
        assert client.get(RUTA).status_code == 200

        destino.unlink()
        response = client.get(RUTA)

        assert response.status_code == 200
        assert len(response.json()["features"]) == 2
        assert response.headers["X-AlertaV-Hazard-Stale"] == "true"

    def test_sirve_la_ultima_copia_buena_si_el_artefacto_queda_corrupto(
        self, hazard: Any
    ) -> None:
        """El caso realista: un `COPY` de Docker o una sincronización a medias."""
        client, destino = hazard
        _escribir(destino, _artefacto())
        client.get(RUTA)

        destino.write_text('{"type": "FeatureCol', encoding="utf-8")
        os.utime(destino, (1_800_000_000, 1_800_000_000))
        response = client.get(RUTA)

        assert response.status_code == 200
        assert response.headers["X-AlertaV-Hazard-Stale"] == "true"
        assert len(response.json()["features"]) == 2

    def test_un_artefacto_vacio_no_se_sirve_como_ausencia_de_amenaza(
        self, hazard: Any
    ) -> None:
        """Cero celdas no es «acá no tiembla»: es un artefacto mal generado.

        Pintar un mapa limpio sobre la V Región sería mentir hacia el lado
        tranquilizador, que es el único lado en el que este sistema no puede
        equivocarse.
        """
        client, destino = hazard
        _escribir(destino, _artefacto(features=0))

        response = client.get(RUTA)

        assert response.status_code == 502
        assert "celda" in json.dumps(response.json()["error"]["detail"])


class TestErrorExplicito:
    def test_sin_artefacto_y_sin_cache_responde_502_con_sobre(
        self, hazard: Any
    ) -> None:
        """Ni 404 desnudo ni volcado de pila: el sobre de error del proyecto."""
        client, _ = hazard

        response = client.get(RUTA)

        assert response.status_code == 502
        error = response.json()["error"]
        assert error["code"] == "collector_error"
        # El mensaje tiene que decir la única acción que arregla esto.
        assert "fetch_seismic_hazard" in error["message"]
        assert error["detail"]["regenerar"] == "python -m scripts.fetch_seismic_hazard"

    def test_el_502_no_filtra_una_traza(self, hazard: Any) -> None:
        client, _ = hazard

        cuerpo = client.get(RUTA).text

        assert "Traceback" not in cuerpo
        assert "app/services/hazard_service.py" not in cuerpo


class TestRuta:
    def test_la_capa_de_amenaza_esta_publicada_en_el_esquema(self) -> None:
        """Si la ruta desaparece del esquema, el frontend se queda sin capa."""
        assert RUTA in app.openapi()["paths"]
