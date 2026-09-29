#!/usr/bin/env bash
#
# Prueba el proxy chileno desde la propia VM. Lo corre `instalar.sh` al final;
# también se puede correr solo, cuando se quiera:
#
#   sudo bash probar.sh
#
# Comprueba las cuatro cosas que importan:
#   1. Esval responde a través del proxy (API y KML): la IP es chilena.
#   2. Otro destino cualquiera se rechaza (403): la lista funciona.
#   3. Sin clave se rechaza (407): la contraseña funciona.
#   4. tinyproxy escucha en el puerto configurado.
# El firewall se prueba desde afuera (README.md, paso 7): desde acá no se ve.

set -euo pipefail

CONF="${CONF:-/etc/tinyproxy/tinyproxy.conf}"
[[ -r "$CONF" ]] || { echo "ERROR: no puedo leer $CONF (¿sudo?)" >&2; exit 1; }

PUERTO="$(awk '$1 == "Port" { print $2; exit }' "$CONF")"
CLAVE="$(awk '$1 == "BasicAuth" { print $3; exit }' "$CONF")"
USUARIO="$(awk '$1 == "BasicAuth" { print $2; exit }' "$CONF")"
PROXY="http://127.0.0.1:${PUERTO}"
AGENTE="AlertaV/proxy-cl prueba (+https://github.com/EmersonDEVRegion/AlertaV)"
API="https://ov.esval.cl/api-ov/api/EstadoDeServicio/CortesActivos"
KML="https://tupuntodeagua.esval.cl/script/generaKmlZonasCorte.aspx?region=5&bbox=-72.0,-33.8,-69.8,-32.0"

fallas=0
bien() { echo "  OK     $*"; }
mal() { echo "  FALLA  $*"; fallas=$((fallas + 1)); }

# Código del CONNECT (lo que responde el proxy) y código final (lo que responde
# el destino). --noproxy '' para que ninguna variable de entorno se lo salte.
pedir() {
  curl -s --noproxy '' -o /dev/null -A "$AGENTE" --max-time 30 \
    -w '%{http_connect} %{http_code}' "$@" 2>/dev/null || true
}

if ! command -v ss >/dev/null; then
  echo "  (sin 'ss': no se revisa el puerto, sólo las respuestas)"
elif ss -ltn "sport = :${PUERTO}" | grep -q LISTEN; then
  bien "tinyproxy escucha en el puerto ${PUERTO}"
else
  mal "nadie escucha en el puerto ${PUERTO}: systemctl status tinyproxy"
fi

read -r conexion codigo <<<"$(pedir -x "http://${USUARIO}:${CLAVE}@127.0.0.1:${PUERTO}" "$API")"
if [[ "$codigo" == 200 ]]; then
  bien "API de Esval a través del proxy: 200"
else
  mal "API de Esval: CONNECT ${conexion}, respuesta ${codigo} (¿la VM está en Chile?)"
fi

read -r conexion codigo <<<"$(pedir -x "http://${USUARIO}:${CLAVE}@127.0.0.1:${PUERTO}" "$KML")"
if [[ "$codigo" == 200 ]]; then
  bien "KML del visor a través del proxy: 200"
else
  mal "KML del visor: CONNECT ${conexion}, respuesta ${codigo}"
fi

read -r conexion _ <<<"$(pedir -x "http://${USUARIO}:${CLAVE}@127.0.0.1:${PUERTO}" https://example.com/)"
if [[ "$conexion" == 403 ]]; then
  bien "otro destino se rechaza: 403"
else
  mal "otro destino respondió ${conexion} en vez de 403: revisa /etc/tinyproxy/filter"
fi

read -r conexion _ <<<"$(pedir -x "$PROXY" "$API")"
if [[ "$conexion" == 407 ]]; then
  bien "sin clave se rechaza: 407"
else
  mal "sin clave respondió ${conexion} en vez de 407: revisa BasicAuth"
fi

if (( fallas > 0 )); then
  echo "${fallas} prueba(s) fallaron." >&2
  exit 1
fi
echo "Todo en orden."
