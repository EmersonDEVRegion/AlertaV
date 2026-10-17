#!/usr/bin/env python3
"""Lista los vínculos entre familias para revisarlos a mano. Sólo lectura.

    python -m scripts.revisar_relaciones --dias 7

Es la revisión de la semana en sombra (`CORRELATION_RELACIONES=sombra`): para
cada par muestra los dos hechos, la distancia y el desfase, y al final cuántos
hubo por regla. Con eso se decide si pasan a `visible`, si alguna regla se
ajusta o si se apaga. Usa la base de `backend/.env` (la de producción, para
leer lo real); no escribe nada.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import AsyncSessionLocal, dispose_engine  # noqa: E402

CONSULTA = f"""
SELECT r.regla, r.distancia_m, r.desfase_s, r.creado_en,
       a.code AS code_a, a.title AS titulo_a, a.commune AS comuna_a, a.first_seen_at AS hora_a,
       b.code AS code_b, b.title AS titulo_b,
       e.type::text AS tipo_e, left(e.text, 120) AS texto_e
FROM {settings.DB_SCHEMA}.incident_relations r
JOIN {settings.DB_SCHEMA}.incidents a ON a.id = r.incident_id
LEFT JOIN {settings.DB_SCHEMA}.incidents b ON b.id = r.otro_incident_id
LEFT JOIN {settings.DB_SCHEMA}.raw_events e ON e.id = r.otro_event_id
WHERE r.creado_en >= now() - make_interval(days => :dias)
ORDER BY a.first_seen_at DESC
"""


async def revisar(dias: int) -> None:
    try:
        async with AsyncSessionLocal() as session:
            filas = (await session.execute(text(CONSULTA), {"dias": dias})).all()
    finally:
        await dispose_engine()
    por_regla: Counter[str] = Counter()
    for f in filas:
        por_regla[f.regla] += 1
        otro = f"{f.code_b} {f.titulo_b or ''}" if f.code_b else f"[{f.tipo_e}] {f.texto_e or ''}"
        minutos = round(f.desfase_s / 60)
        print(
            f"{f.hora_a:%d-%m %H:%M}  {f.regla:<26} {round(f.distancia_m):>4} m  "
            f"{minutos:+4d} min  {f.code_a} {f.titulo_a or ''} ({f.comuna_a or 'sin comuna'})"
        )
        print(f"{'':>44}↳ {otro.strip()}")
    print()
    print(f"{len(filas)} vínculos en {dias} días")
    for regla, n in por_regla.most_common():
        print(f"  {regla}: {n}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dias", type=int, default=7)
    asyncio.run(revisar(parser.parse_args().dias))


if __name__ == "__main__":
    main()
