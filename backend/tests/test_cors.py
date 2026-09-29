"""CORS: lo que el navegador puede leer desde el frontend, que vive en otro origen."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


def test_expone_retry_after_para_que_el_cliente_espere_lo_pedido():
    # El frontend (Vercel) y la API (Render) están en orígenes distintos: una
    # cabecera que no se expone no existe para `fetch`. Sin `Retry-After`, un
    # 429 se reintenta con el backoff propio del cliente en vez del del servidor.
    origen = settings.CORS_ORIGINS[0]
    respuesta = TestClient(app).get("/api/v1/health", headers={"Origin": origen})

    assert respuesta.headers.get("access-control-allow-origin") == origen
    expuestas = {h.strip().lower() for h in respuesta.headers["access-control-expose-headers"].split(",")}
    assert {"retry-after", "etag"} <= expuestas
