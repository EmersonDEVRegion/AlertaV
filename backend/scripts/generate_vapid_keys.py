"""Genera el par de claves VAPID para las notificaciones push.

    python scripts/generate_vapid_keys.py

Imprime la línea que va en las variables de entorno del backend (Render) y, de
paso, la clave pública, que NO hace falta configurar en ningún lado: el backend
la deriva de la privada y la PWA la pide a `/api/v1/push/status`. Se muestra
sólo para poder reconocerla en las herramientas de desarrollo del navegador.

Una vez en producción, **no la regeneres**: cada suscripción queda atada a la
clave pública con que se creó, y cambiarla deja mudos a todos los teléfonos
suscritos hasta que cada uno vuelva a abrir la app.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.push.webpush import VapidKeys


def main() -> None:
    keys = VapidKeys.generate()
    print("# Backend (Render → Environment). Trátala como una contraseña.")
    print(f"VAPID_PRIVATE_KEY={keys.private_b64url}")
    print("VAPID_SUBJECT=mailto:<correo-de-contacto>")
    print()
    print("# Pública, derivada de la anterior. No se configura: es informativa.")
    print(f"# {keys.public_b64url}")


if __name__ == "__main__":
    main()
