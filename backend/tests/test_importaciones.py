"""Cada punto de entrada importa limpio en un intérprete nuevo.

El 2026-09-28 un import agregado en `correlation/communes.py` cerró un ciclo
(`collectors` → `base` → `services` → `correlation` → `lugares` →
`collectors.weather` → `base`) que sólo se manifestaba según el ORDEN de
importación: la suite completa pasaba —importa `app.main` primero— y
`python -m app.workers` moría en producción. Por eso esto corre en
subprocesos: dentro de pytest los módulos ya están cargados y el ciclo no se ve.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "modulo",
    [
        "app.workers",
        "app.collectors",
        "app.collectors.runner",
        "app.services",
        "app.services.correlation.runner",
        "app.services.push.runner",
        "app.main",
    ],
)
def test_el_modulo_importa_en_un_interprete_nuevo(modulo):
    resultado = subprocess.run(
        [sys.executable, "-c", f"import {modulo}"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert resultado.returncode == 0, resultado.stderr[-2000:]
