# Proxy en Chile para Esval (Oracle Cloud)

Esval sólo responde a IP chilenas. Fuera de Chile sus dos hosts descartan la
conexión en silencio, y Render no tiene regiones en Sudamérica, así que el
collector `esval_cortes_agua` sale por este proxy. El diagnóstico está en
`plan.md`, §S1.

```
Render (EE. UU.) ──CONNECT──▶ tinyproxy (Oracle, Chile) ──TLS──▶ ov.esval.cl
                                                          └──TLS──▶ tupuntodeagua.esval.cl
```

**Lo que hace y lo que no:**

- Sólo abre túneles `CONNECT` al puerto 443 de esos dos hosts. Cualquier otro destino recibe un 403.
- Sólo acepta clientes de las IP de salida de Render, y sólo con usuario y clave.
- **No ve ni toca los datos.** El TLS va de Render a Esval de punta a punta, y httpx valida el certificado de Esval.
- El `User-Agent` sigue diciendo quién es AlertaV, y la cadencia es la misma: 2 peticiones cada 10 minutos.

| Archivo | Qué es |
|---|---|
| `instalar.sh` | Instala o reinstala todo en la VM. Es idempotente. |
| `probar.sh` | Prueba el proxy desde la VM. Lo llama `instalar.sh` al final. |
| `desplegar.ps1` | Desde tu Windows: sube la carpeta, instala y prueba que el puerto esté cerrado para tu PC. |
| `tinyproxy.conf`, `filter` | Plantilla y lista de hosts. |

## 1. Cuenta de Oracle Cloud

1. Crea la cuenta en <https://signup.cloud.oracle.com> (*Free Tier*).
2. **Región de origen: `Chile Central (Santiago)` o `Chile West (Valparaíso)`.** Se elige al registrarse y **no se puede cambiar**. Las VM gratis sólo se crean ahí. Las dos se probaron contra Esval el 29-09 y responden.
3. Oracle pide una tarjeta para verificar la identidad. Mientras uses sólo recursos *Always Free*, no cobra.

## 2. Que Oracle no la dé por abandonada

Oracle **recupera** las VM gratis que pasan 7 días «ociosas». Cuenta como ociosa si se cumplen todas estas condiciones a la vez:

- la CPU en el percentil 95 está bajo 20 %;
- la red está bajo 20 %;
- **y, sólo en las VM Ampere A1,** la memoria también está bajo 20 %.

Un proxy que atiende 2 peticiones cada 10 minutos cumple las dos primeras siempre. Lo que la salva es la memoria:

- **Usa una VM Ampere A1 con la menor memoria que te deje la consola** (1 OCPU; 1 GB si se puede, si no 2 GB).
- Ubuntu en reposo usa unos 300–400 MB, así que con 1–2 GB queda sobre 20 % y la VM no cuenta como ociosa.
- Con los 6 GB que la consola propone por defecto, queda bajo 20 % y sí cuenta.
- La VM AMD gratis (`E2.1.Micro`) no tiene el criterio de memoria: ésa sí la recuperarían.

**Compruébalo al día siguiente:** *Compute → Instances → la VM → Metrics → Memory Utilization*. Tiene que estar sobre 20 %.

Pasar la cuenta a *Pay As You Go* también protege, según varios reportes: los recursos *Always Free* siguen sin costo. No lo pude confirmar en la documentación de Oracle. Si lo haces, crea antes un presupuesto con alerta de US$ 1 (*Billing → Budgets*).

## 3. Crear la VM

*Compute → Instances → Create instance*:

| Campo | Valor |
|---|---|
| Nombre | `alertav-proxy-cl` |
| Imagen | **Canonical Ubuntu 24.04** (la variante `aarch64`, que es la de A1) |
| Shape | **VM.Standard.A1.Flex**, 1 OCPU, memoria mínima (paso 2) |
| Red | La VCN por defecto, subred **pública**, con *Assign a public IPv4 address* |
| SSH | Sube tu llave pública. Si no tienes una, en PowerShell: `ssh-keygen -t ed25519` y usa `~\.ssh\id_ed25519.pub` |

- Si responde *Out of capacity*, reintenta más tarde o cambia de *Availability Domain*. Es común en A1.
- Anota la **IP pública**. Si alguna vez la VM se borra y se vuelve a crear, la IP cambia y hay que actualizar `ESVAL_PROXY_URL` en Render.

## 4. Las IP de salida de Render

En Render: *Dashboard → el servicio de AlertaV → Connect → Outbound*. Copia los rangos, por ejemplo `a.b.c.d/nn`.

- Son de la región del servicio, así que valen para todos los servicios de AlertaV en esa región.
- **Son compartidas** con otros clientes de Render de la misma región. El firewall achica la superficie, pero la barrera que manda es la clave.

## 5. Abrir el puerto en Oracle (sólo para Render)

*Networking → Virtual cloud networks → la VCN → Security Lists → Default Security List → Add Ingress Rules*. Agrega **una regla por cada rango de Render**:

| Campo | Valor |
|---|---|
| Source CIDR | el rango de Render |
| IP Protocol | TCP |
| Destination Port Range | `8888` |

**Nunca** pongas `0.0.0.0/0`: sería un proxy para cualquiera. `instalar.sh` se niega a usarlo, pero la consola no.

## 6. Instalar

Desde la carpeta del repo, en PowerShell:

```powershell
.\infra\proxy-cl\desplegar.ps1 -Ip <ip-de-la-vm> -RenderCidrs "a.b.c.d/nn","e.f.g.h/nn"
```

El script hace esto:

1. sube `infra/proxy-cl` a la VM;
2. corre `instalar.sh`, que instala tinyproxy y las actualizaciones automáticas, genera una clave de 48 caracteres, ajusta el firewall de la VM y prueba el proxy desde adentro;
3. comprueba desde tu PC que el puerto **no** responde. Tu IP no es de Render, así que tiene que estar cerrado.

Al final imprime la línea para Render:

```
http://alertav:<clave>@<ip>:8888
```

Queda también en `/root/alertav-esval-proxy-url.txt` en la VM, legible sólo por root.

A mano, sin el script:

```bash
scp -r infra/proxy-cl ubuntu@<ip>:
ssh ubuntu@<ip>
sudo RENDER_CIDRS="a.b.c.d/nn e.f.g.h/nn" bash proxy-cl/instalar.sh
```

**Sobre el firewall de la VM:** las imágenes Ubuntu de Oracle traen reglas iptables propias, y Oracle pide no usar `ufw` en ellas. `instalar.sh` agrega una cadena `ALERTAV-PROXY` antes del `REJECT` de la imagen y la guarda con `netfilter-persistent`.

## 7. Conectar Render

1. En Render, en el servicio que corre los collectors: *Environment → Add Environment Variable → `ESVAL_PROXY_URL`*, como **secreto**, con la línea del paso 6.
2. Guarda y deja que redespliegue.
3. Espera la siguiente corrida de `esval_cortes_agua` (cada 10 minutos). En `collector_runs` tiene que quedar en `success`, o en `partial` si el KML falló, con `params.via = "proxy-cl"`.
4. `GET /api/v1/events/water-cuts/geojson` empieza a traer cortes cuando Esval tiene alguno vigente.

Si falla, el mensaje dice por dónde se cortó. Con el proxy configurado, todos empiezan con `esval vía proxy-cl:`; la causa va entre paréntesis:

| Mensaje en `collector_runs` | Qué pasa |
|---|---|
| `falta ESVAL_PROXY_URL` | La variable no llegó al servicio que corre los collectors. |
| `(ProxyError: 407 …)` o `(ProxyError: 401 Unauthorized)` | Falta la clave o está mal: vuelve a copiar la línea del paso 6. |
| `(ConnectTimeout …)` o `(ConnectError …)` | No se llega a la VM: la Security List (paso 5), el firewall de la VM o la VM apagada. |
| `(ProxyError: 500 …)` | La VM no llegó a Esval. Corre `probar.sh` en la VM. |
| `(ProxyError: 403 Filtered)` | El destino no está en `filter`. No debería pasar con las URL por defecto. |
| `(HTTP 5xx)`, `HTTP 4xx` o `se recibió HTML` | Se llegó a Esval: el problema es de Esval. |

## 8. Operación

- **Probar:** `ssh ubuntu@<ip> sudo bash proxy-cl/probar.sh`.
- **Ver quién se conecta:** `sudo tail -f /var/log/tinyproxy/tinyproxy.log` (una línea por conexión, sin contenido).
- **Cambiar la clave:** `desplegar.ps1 … -RotarClave` (o `sudo ROTAR=1 RENDER_CIDRS="…" bash proxy-cl/instalar.sh`) y actualiza `ESVAL_PROXY_URL` en Render. La anterior deja de servir en el acto. Sin `-RotarClave`, reinstalar conserva la clave.
- **Render cambió sus rangos:** corre `instalar.sh` con los nuevos `RENDER_CIDRS` y actualiza la Security List.
- **Actualizaciones:** `unattended-upgrades` aplica los parches de seguridad solo, todos los días.

## Sobre la clave

Entre Render y Oracle, la clave viaja en texto claro, dentro del `CONNECT` (*Basic auth*). El contenido del túnel sí va cifrado. Quien la intercepte sólo podría:

- usar el proxy desde las IP de Render;
- y sólo hacia los dos hosts de Esval.

El daño posible es que Esval bloquee la IP de la VM. Si sospechas que se filtró, cámbiala (paso 8).
