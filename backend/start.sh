#!/usr/bin/env bash
# =============================================================================
#  AlertaV — orquestador de un solo contenedor
#
#  Arranca y supervisa los tres procesos que en local se ejecutan por separado:
#
#    1. uvicorn                             — la API que consume el frontend
#    2. app.collectors.runner --loop        — recolección de FIRMS/CONAF/SENAPRED
#    3. app.services.correlation.runner     — agrupación de señales en incidentes
#
#  Se eligió bash y no supervisord por una razón concreta: la instancia gratuita
#  tiene 512 MB de RAM y 0.1 vCPU, y los tres procesos Python ya ocupan unos
#  350 MB. Un supervisor en Python encima serían 25 MB más y un archivo de
#  configuración que oculta, tras una capa de indirección, exactamente lo que
#  hacen estas 40 líneas.
#
#  Tres propiedades que este script sí garantiza:
#
#    - Todo sale por stdout/stderr sin buffer. Es la única forma en que Render o
#      Koyeb pueden mostrar logs: no hay disco donde ir a buscarlos.
#    - Cada worker se reinicia solo, con backoff exponencial. Los runners ya
#      atrapan sus propias excepciones; esto cubre la muerte dura del proceso
#      (OOM killer, por ejemplo, que en 512 MB es un escenario real).
#    - SIGTERM se propaga a los hijos. La plataforma da 30 segundos de gracia
#      antes del SIGKILL; los runners cierran su pool de conexiones al recibirlo,
#      y una conexión cerrada en orden es una conexión menos colgada en el
#      pooler de Supabase tras cada despliegue.
# =============================================================================

# Sin `-e`: en un supervisor, que un hijo falle es el caso que se está
# gestionando, no motivo para abortar. `-u` y `pipefail` sí, para que una
# variable mal escrita se note en el arranque y no en la primera pasada.
set -uo pipefail

readonly PORT="${PORT:-8000}"
readonly RUN_MIGRATIONS="${RUN_MIGRATIONS:-0}"
readonly ENABLE_COLLECTORS="${ENABLE_COLLECTORS:-1}"
readonly ENABLE_CORRELATION="${ENABLE_CORRELATION:-1}"
readonly BACKOFF_MAX_SECONDS="${BACKOFF_MAX_SECONDS:-60}"

# Mismo formato JSON que app/core/logging.py, para que un solo filtro sirva
# sobre toda la salida del contenedor.
log() {
    printf '{"ts":"%s","level":"%s","logger":"orchestrator","message":"%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2"
}

# -----------------------------------------------------------------------------
#  Supervisión de un proceso
# -----------------------------------------------------------------------------
# Se ejecuta en segundo plano, una instancia por worker. Mantiene vivo su hijo y
# lo reinicia con espera creciente. El backoff importa: si el worker muere
# porque la base no responde, reintentar cada 100 ms sólo agrega carga al
# incidente que ya está ocurriendo.
supervise() {
    local name="$1"
    shift

    local child=0
    local stopping=0
    local backoff=2

    # El trap corre en esta subshell y le pasa la señal al hijo. Sin él, la
    # muerte del supervisor dejaría al worker huérfano corriendo hasta el
    # SIGKILL de la plataforma, escribiendo en la base durante el despliegue.
    trap 'stopping=1; [ "$child" -ne 0 ] && kill -TERM "$child" 2>/dev/null' TERM INT

    while true; do
        log INFO "$name: arrancando"
        "$@" &
        child=$!

        wait "$child"
        local code=$?
        child=0

        if [ "$stopping" -eq 1 ]; then
            log INFO "$name: detenido limpiamente"
            return 0
        fi

        log ERROR "$name: terminó con código $code; reintento en ${backoff}s"
        sleep "$backoff"
        if [ "$backoff" -lt "$BACKOFF_MAX_SECONDS" ]; then
            backoff=$(( backoff * 2 ))
            [ "$backoff" -gt "$BACKOFF_MAX_SECONDS" ] && backoff="$BACKOFF_MAX_SECONDS"
        fi
    done
}

# -----------------------------------------------------------------------------
#  Apagado ordenado
# -----------------------------------------------------------------------------
PIDS=()
SHUTDOWN=0

shutdown() {
    [ "$SHUTDOWN" -eq 1 ] && return 0
    SHUTDOWN=1
    log INFO "señal de término recibida; deteniendo los tres procesos"
    for pid in "${PIDS[@]}"; do
        kill -TERM "$pid" 2>/dev/null
    done
}
trap shutdown TERM INT

# -----------------------------------------------------------------------------
#  Migraciones (opcional)
# -----------------------------------------------------------------------------
# Apagado por defecto, y no por descuido. Aplicar el esquema en cada arranque
# significa que un despliegue automático puede alterar la base de producción sin
# que nadie mire; y si la plataforma reinicia el contenedor durante una
# migración larga, queda a medias. Lo recomendado es correr
# `alembic upgrade head` a mano desde el equipo local contra Supabase. Este
# interruptor existe para el primer despliegue y para entornos de prueba.
if [ "$RUN_MIGRATIONS" = "1" ]; then
    log INFO "aplicando migraciones (alembic upgrade head)"
    if ! alembic upgrade head; then
        log ERROR "las migraciones fallaron; el contenedor no arranca"
        exit 1
    fi
    log INFO "migraciones al día"
fi

# -----------------------------------------------------------------------------
#  Arranque
# -----------------------------------------------------------------------------
log INFO "AlertaV: iniciando contenedor (puerto ${PORT})"

# --proxy-headers + --forwarded-allow-ips: detrás del edge de Koyeb/Render el
# TLS termina en el proxy y la app ve HTTP plano. Sin esto, las URLs que
# construye FastAPI salen con esquema http:// y el navegador bloquea la petición
# por contenido mixto.
# --workers 1: con 0.1 vCPU, más procesos de uvicorn no reparten trabajo, sólo
# reparten la misma décima de núcleo y duplican la memoria.
supervise api \
    uvicorn app.main:app \
        --host 0.0.0.0 \
        --port "$PORT" \
        --workers 1 \
        --proxy-headers \
        --forwarded-allow-ips '*' \
        --no-use-colors &
PIDS+=($!)

if [ "$ENABLE_COLLECTORS" = "1" ]; then
    supervise collectors python -m app.collectors.runner --loop &
    PIDS+=($!)
else
    log WARN "collectors desactivados por ENABLE_COLLECTORS=0"
fi

if [ "$ENABLE_CORRELATION" = "1" ]; then
    supervise correlation python -m app.services.correlation.runner --loop &
    PIDS+=($!)
else
    log WARN "motor de correlación desactivado por ENABLE_CORRELATION=0"
fi

log INFO "procesos supervisados: ${#PIDS[@]}"

# -----------------------------------------------------------------------------
#  Espera
# -----------------------------------------------------------------------------
# `wait` se interrumpe cuando llega una señal atrapada, así que hay que volver a
# esperar hasta que no quede ningún supervisor vivo. Un `wait` suelto daría por
# terminado el contenedor en cuanto llegue el SIGTERM, matando a los hijos antes
# de que cierren sus conexiones.
while true; do
    wait -n 2>/dev/null
    alive=0
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            alive=1
            break
        fi
    done
    [ "$alive" -eq 0 ] && break
done

log INFO "AlertaV: contenedor detenido"
