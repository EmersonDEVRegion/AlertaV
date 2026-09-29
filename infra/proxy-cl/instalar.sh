#!/usr/bin/env bash
#
# Instala (o reinstala) el proxy chileno de AlertaV en una VM Ubuntu.
# Pensado para Oracle Cloud (Always Free, Santiago o Valparaíso); sirve en
# cualquier Ubuntu 22.04/24.04. Es idempotente: se puede correr de nuevo para
# cambiar la clave o las IP de Render.
#
#   sudo RENDER_CIDRS="a.b.c.d/nn e.f.g.h/nn" bash instalar.sh
#
# Variables:
#   RENDER_CIDRS   Obligatoria. Rangos de salida de Render, separados por
#                  espacios (Dashboard → servicio → Connect → Outbound).
#   PROXY_CLAVE    Opcional. Sólo letras y números, 24 o más. Si falta, se
#                  conserva la que ya estaba instalada; si no había ninguna,
#                  se genera una (48 caracteres hex).
#   ROTAR=1        Opcional. Genera una clave nueva aunque ya hubiera una.
#   PUERTO         Opcional. 8888 por defecto.
#
# Con `--solo-archivos DIR` no instala nada: deja en DIR la configuración que
# instalaría. Sirve para revisarla (y para los tests).
#
# Qué NO hace: abrir el puerto en la Security List de Oracle. Eso se hace en la
# consola (README.md, paso 5): es la primera de las dos barreras de red.

set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUERTO="${PUERTO:-8888}"
RENDER_CIDRS="${RENDER_CIDRS:-}"
PROXY_CLAVE="${PROXY_CLAVE:-}"
CADENA="ALERTAV-PROXY"
USUARIO="alertav"

falla() { echo "ERROR: $*" >&2; exit 1; }
paso() { echo; echo "==> $*"; }

validar() {
  if ! [[ "$PUERTO" =~ ^[0-9]+$ ]] || (( PUERTO < 1024 || PUERTO > 65535 )); then
    falla "PUERTO tiene que ser un número entre 1024 y 65535"
  fi
  [[ -n "$RENDER_CIDRS" ]] \
    || falla "falta RENDER_CIDRS: los rangos de salida de Render (README.md, paso 4)"
  local cidr
  for cidr in $RENDER_CIDRS; do
    [[ "$cidr" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])$ ]] \
      || falla "RENDER_CIDRS: '$cidr' no es un rango IPv4 (a.b.c.d/nn)"
    [[ "$cidr" != "0.0.0.0/0" ]] \
      || falla "RENDER_CIDRS: 0.0.0.0/0 abriría el proxy a todo internet"
  done
  # Reinstalar para cambiar las IP de Render no tiene por qué cambiar la clave:
  # obligaría a tocar Render cada vez.
  if [[ -z "$PROXY_CLAVE" && "${ROTAR:-0}" != 1 && -r /etc/tinyproxy/tinyproxy.conf ]]; then
    PROXY_CLAVE="$(awk -v u="$USUARIO" '$1 == "BasicAuth" && $2 == u { print $3; exit }' \
      /etc/tinyproxy/tinyproxy.conf)"
  fi
  if [[ -z "$PROXY_CLAVE" ]]; then
    PROXY_CLAVE="$(openssl rand -hex 24)"
    CLAVE_NUEVA=1
  fi
  [[ "$PROXY_CLAVE" =~ ^[A-Za-z0-9]{24,}$ ]] \
    || falla "PROXY_CLAVE: sólo letras y números, 24 o más (va dentro de una URL)"
}

# La configuración, con la clave y las IP. Escribe en $1.
generar() {
  local destino="$1" allow="" cidr
  for cidr in $RENDER_CIDRS; do
    allow+="Allow ${cidr}"$'\n'
  done
  mkdir -p "$destino"
  install -m 0644 "$AQUI/filter" "$destino/filter"
  # awk y no sed: la lista de Allow tiene saltos de línea.
  awk -v puerto="$PUERTO" -v clave="$PROXY_CLAVE" -v allow="${allow%$'\n'}" '
    { gsub(/@PUERTO@/, puerto); gsub(/@CLAVE@/, clave) }
    /^@ALLOW@$/ { print allow; next }
    { print }
  ' "$AQUI/tinyproxy.conf" > "$destino/tinyproxy.conf"
  chmod 0640 "$destino/tinyproxy.conf"
}

paquetes() {
  paso "Paquetes: tinyproxy, actualizaciones automáticas y reglas persistentes"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q tinyproxy unattended-upgrades iptables-persistent curl openssl
  # Parches de seguridad solos, todos los días. Ubuntu los trae encendidos; se
  # escribe igual por si la imagen los apagó.
  cat > /etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT
}

configurar() {
  paso "Configuración de tinyproxy"
  generar /etc/tinyproxy
  chown root:tinyproxy /etc/tinyproxy/tinyproxy.conf
  systemctl enable tinyproxy >/dev/null
  systemctl restart tinyproxy
  systemctl is-active --quiet tinyproxy || falla "tinyproxy no arrancó: journalctl -u tinyproxy"
}

firewall() {
  paso "Firewall de la VM: el puerto $PUERTO sólo para Render"
  # Las imágenes Ubuntu de Oracle traen reglas iptables propias que rechazan todo
  # salvo SSH, y Oracle pide NO usar ufw en ellas. Por eso una cadena propia,
  # insertada antes del REJECT de la imagen, y guardada con netfilter-persistent.
  iptables -N "$CADENA" 2>/dev/null || iptables -F "$CADENA"
  iptables -A "$CADENA" -i lo -j ACCEPT
  local cidr
  for cidr in $RENDER_CIDRS; do
    iptables -A "$CADENA" -s "$cidr" -j ACCEPT
  done
  iptables -A "$CADENA" -j DROP
  if ! iptables -C INPUT -p tcp --dport "$PUERTO" -j "$CADENA" 2>/dev/null; then
    local rechazo
    rechazo="$(iptables -L INPUT --line-numbers -n | awk '$2 == "REJECT" { print $1; exit }')"
    if [[ -n "$rechazo" ]]; then
      iptables -I INPUT "$rechazo" -p tcp --dport "$PUERTO" -j "$CADENA"
    else
      iptables -A INPUT -p tcp --dport "$PUERTO" -j "$CADENA"
    fi
  fi
  netfilter-persistent save >/dev/null
}

resumen() {
  local ip
  ip="$(curl -s --max-time 10 https://api.ipify.org || true)"
  [[ -n "$ip" ]] || ip="<ip-publica-de-la-vm>"
  local url="http://${USUARIO}:${PROXY_CLAVE}@${ip}:${PUERTO}"
  umask 077
  echo "$url" > /root/alertav-esval-proxy-url.txt
  paso "Listo"
  echo "Pega esto en Render como ESVAL_PROXY_URL (secreto):"
  echo
  echo "  $url"
  echo
  echo "Quedó también en /root/alertav-esval-proxy-url.txt (sólo root)."
  if [[ "${CLAVE_NUEVA:-0}" == 1 ]]; then
    echo "La clave es nueva: si había otra en Render, deja de servir ahora."
  fi
}

main() {
  if [[ "${1:-}" == "--solo-archivos" ]]; then
    [[ -n "${2:-}" ]] || falla "uso: $0 --solo-archivos DIR"
    validar
    generar "$2"
    echo "Configuración generada en $2"
    return
  fi
  [[ "$(id -u)" == 0 ]] || falla "hay que correrlo con sudo"
  # shellcheck source=/dev/null
  [[ -r /etc/os-release ]] && . /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || echo "Aviso: probado en Ubuntu; esto es '${ID:-desconocido}'." >&2
  validar
  paquetes
  configurar
  firewall
  paso "Prueba desde la propia VM"
  bash "$AQUI/probar.sh"
  resumen
}

main "$@"
