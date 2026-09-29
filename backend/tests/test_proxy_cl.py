"""El proxy chileno de Esval (`infra/proxy-cl/`) y el collector se ponen de acuerdo.

El proxy sólo deja pasar a los hosts de su lista. Si alguien cambia
`ESVAL_CORTES_URL` o `ESVAL_ZONAS_KML_URL` a otro host sin tocar la lista, la
corrida empezaría a fallar con un 403 del proxy en producción: estos tests lo
atajan antes. Y fijan que `instalar.sh` genere una configuración cerrada.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.core.config import settings

INFRA = Path(__file__).resolve().parents[2] / "infra" / "proxy-cl"


def _patrones() -> list[re.Pattern[str]]:
    lineas = (INFRA / "filter").read_text(encoding="utf-8").splitlines()
    return [re.compile(linea, re.IGNORECASE) for linea in lineas if linea.strip()]


def _pasa(host: str) -> bool:
    return any(patron.search(host) for patron in _patrones())


@pytest.mark.parametrize("url", [settings.ESVAL_CORTES_URL, settings.ESVAL_ZONAS_KML_URL])
def test_el_proxy_deja_pasar_los_hosts_que_pide_el_collector(url):
    host = urlsplit(url).hostname
    assert host and _pasa(host), f"{host} no está en infra/proxy-cl/filter"


@pytest.mark.parametrize(
    "host",
    ["example.com", "esval.cl", "www.esval.cl", "ov.esval.cl.example.com", "xov.esval.cl"],
)
def test_el_proxy_no_deja_pasar_nada_mas(host):
    assert not _pasa(host)


# --- instalar.sh ---------------------------------------------------------------

requiere_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="sin bash")


def _instalar(tmp_path: Path, **entorno: str) -> subprocess.CompletedProcess[str]:
    variables = {k: v for k, v in os.environ.items() if k not in {"PROXY_CLAVE", "RENDER_CIDRS"}}
    variables.update(entorno)
    return subprocess.run(
        ["bash", str(INFRA / "instalar.sh"), "--solo-archivos", str(tmp_path / "conf")],
        env=variables,
        capture_output=True,
        text=True,
        check=False,
    )


@requiere_bash
def test_instalar_genera_una_configuracion_cerrada(tmp_path):
    clave = "a" * 48
    resultado = _instalar(
        tmp_path, RENDER_CIDRS="192.0.2.0/24 198.51.100.0/25", PROXY_CLAVE=clave, PUERTO="8888"
    )
    assert resultado.returncode == 0, resultado.stderr

    conf = (tmp_path / "conf" / "tinyproxy.conf").read_text(encoding="utf-8")
    lineas = {linea.strip() for linea in conf.splitlines()}
    assert "@" not in conf, "quedó un marcador de la plantilla sin reemplazar"
    assert "Port 8888" in lineas
    assert {"Allow 192.0.2.0/24", "Allow 198.51.100.0/25", "Allow 127.0.0.1"} <= lineas
    assert f"BasicAuth alertav {clave}" in lineas
    assert {"ConnectPort 443", "FilterDefaultDeny Yes", "FilterType ere"} <= lineas
    # La configuración trae la clave: no puede quedar legible para cualquiera.
    assert oct((tmp_path / "conf" / "tinyproxy.conf").stat().st_mode & 0o777) == "0o640"


@requiere_bash
@pytest.mark.parametrize(
    ("entorno", "motivo"),
    [
        ({"RENDER_CIDRS": "0.0.0.0/0"}, "todo internet"),
        ({"RENDER_CIDRS": "1.2.3.4"}, "no es un rango"),
        ({"RENDER_CIDRS": ""}, "falta RENDER_CIDRS"),
        ({"RENDER_CIDRS": "192.0.2.0/24", "PROXY_CLAVE": "corta"}, "PROXY_CLAVE"),
        ({"RENDER_CIDRS": "192.0.2.0/24", "PROXY_CLAVE": "b" * 30 + "/:@"}, "PROXY_CLAVE"),
        ({"RENDER_CIDRS": "192.0.2.0/24", "PUERTO": "80"}, "PUERTO"),
    ],
)
def test_instalar_se_niega_a_una_configuracion_abierta(tmp_path, entorno, motivo):
    resultado = _instalar(tmp_path, **entorno)
    assert resultado.returncode != 0
    assert motivo in resultado.stderr
    assert not (tmp_path / "conf" / "tinyproxy.conf").exists()
