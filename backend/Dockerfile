# =============================================================================
#  AlertaV — Backend
#
#  Imagen de un solo contenedor que corre TRES procesos: la API y los dos
#  workers en loop. No es la forma canónica de empaquetar un servicio —lo
#  canónico es un contenedor por proceso— pero las capas gratuitas de Render y
#  Koyeb dan un único contenedor, y un incendio detectado 15 minutos tarde por
#  falta de presupuesto de infraestructura es peor que una imagen impura.
#  La orquestación vive en start.sh.
#
#  Build local:
#    docker build -t alertav-backend .
#    docker run --rm -p 8000:8000 --env-file .env alertav-backend
# =============================================================================

# -----------------------------------------------------------------------------
#  Etapa 1 — builder
#
#  Las dependencias se instalan en un virtualenv propio y no en el Python del
#  sistema. Así la etapa final copia un solo directorio autocontenido y no
#  hereda ni pip ni las cabeceras de compilación.
# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential y libpq-dev son la red de seguridad: hoy todas las
# dependencias traen wheel para cp312 y no se compila nada, pero si mañana una
# versión nueva llega sólo como sdist, el build sigue funcionando en vez de
# fallar con un "error: command 'gcc' failed". Nada de esto llega a la imagen
# final.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Sólo el archivo de dependencias primero: mientras no cambie, Docker reutiliza
# esta capa y un cambio de código no vuelve a bajar todo PyPI.
COPY requirements-prod.txt ./
RUN pip install --upgrade pip \
 && pip install -r requirements-prod.txt


# -----------------------------------------------------------------------------
#  Etapa 2 — runtime
# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

# PYTHONUNBUFFERED=1 es la línea que hace que esta imagen sea depurable: sin
# ella Python almacena stdout en un buffer de 8 KB cuando no escribe a una
# terminal, y los logs de la plataforma se quedan vacíos durante minutos —justo
# mientras uno intenta entender por qué el arranque falló.

# libpq5 es la librería cliente de Postgres en tiempo de ejecución (la usa
# psycopg2 para Alembic). ca-certificates es obligatoria: sin ella no hay TLS
# contra Supabase ni contra las APIs de NASA, CONAF y SENAPRED.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Usuario sin privilegios. Un collector consume HTML y JSON de terceros: si
# alguna vez se logra ejecución de código a través de esa superficie, que no sea
# como root.
RUN useradd --create-home --uid 10001 alertav

# Código y todo lo que necesitan las migraciones.
COPY --chown=alertav:alertav app/ ./app/
# Artefactos geoespaciales estáticos (mapa de amenaza sísmica, etc.). Van fuera
# de `app/` porque son datos, no código, y se sirven en `/static`. El directorio
# viaja siempre en git gracias a su `.gitkeep`: sin él, este COPY fallaría el
# build en un checkout donde todavía no se ha generado ningún artefacto.
COPY --chown=alertav:alertav static/ ./static/
COPY --chown=alertav:alertav migrations/ ./migrations/
COPY --chown=alertav:alertav alembic.ini pyproject.toml ./
COPY --chown=alertav:alertav start.sh ./

# El repositorio se edita en Windows. Si start.sh viaja con CRLF, el kernel
# intenta ejecutar `/usr/bin/env bash\r` y el contenedor muere con un
# "no such file or directory" que no menciona los saltos de línea en ninguna
# parte. .gitattributes fuerza LF en el repositorio; esto cubre el caso de un
# checkout hecho antes de ese archivo.
RUN sed -i 's/\r$//' start.sh && chmod +x start.sh

USER alertav

EXPOSE 8000

# tini como PID 1: reaparea los procesos huérfanos que dejan los reinicios de
# los workers y propaga las señales al script. Bash sabe hacer ambas cosas, pero
# no con la misma prolijidad cuando es PID 1.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["./start.sh"]
