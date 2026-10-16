# Apify: despachos de Bomberos y tránsito por X

Apify raspa cuentas de X y avisa al backend cuando termina. Es la única entrada
de los despachos de Bomberos y de los tuits de tránsito del MTT.

**Dos Tasks, un Actor (`xquik/x-tweet-scraper`), dos Schedules:**

| Task | Schedule | Qué pide | Entrega en |
|---|---|---|---|
| `alertav-bomberos` | `alertav`, cada 30 min | lo publicado en los **últimos 45 min** (`within_time`), hasta 6 por cuenta y 17 en total | `POST /api/v1/apify/webhook` |
| `alertav-canario` | `alertav-canario`, 12:10 cada día | los 2 últimos tuits de cada cuenta (12 en total), **sin ventana** | el mismo webhook |

Las seis cuentas, en los dos:

- Las cinco centrales: `@CGI_CBV` (Valparaíso), `@CBVM132` (Viña del Mar y
  Concón), `@despachoscbla` (Los Andes y Calle Larga), `@CBQuilpue` y
  `@cbquillota`. Entran como `bomberos`, confianza **1.00**.
- `@TTIValparaiso` (TransporteInforma Región de Valparaíso, del MTT). Entra como
  `transporte_informa`, confianza **0.80**, en la corrida `transporte_informa_x`.

## Cómo funciona

- **Ventana.** El Task principal pide sólo lo publicado en los últimos 45
  minutos (30 de cadencia + 15 de margen). xquik aplica el filtro **antes de
  cobrar**: una corrida sin tuits nuevos cuesta 0 tuits y ~US$ 0,0001 de
  plataforma. Una entrega vacía es **calma**.
- **Canario.** Como una entrega vacía no dice nada, la ceguera la mide un Task
  aparte, una vez al día y sin ventana. El backend lo reconoce por
  `APIFY_X_CANARIO_IDS`, lo registra como `bomberos_apify_canario` y **sólo
  ahí** exige ver cada cuenta de `APIFY_X_CUENTAS_ESPERADAS`. Un lote que trae
  items y todos son relleno (`{"noResults": true}`) es `degraded` en cualquiera
  de los dos.
- **Seguimientos.** «SALE M-34 A CLAVE 15 …» no es un despacho nuevo: sus
  unidades se anexan al original (mismo Cuerpo, clave y calles, dentro de 90
  min) sin modelo ni Nominatim.
- **Tránsito.** El backend separa los tuits de `APIFY_X_TRANSITO_HANDLES` antes
  de buscar tabla de claves. Pasan por la tubería del MTT
  (`clasificar_transito`: accidente → mapa; desvío, corte o faena → capa de
  cortes; lo demás fuera), Gemini y Nominatim, en su propia corrida.
- **Fechas.** El formato de X (`Tue Sep 29 18:50:12 +0000 2026`) se parsea a
  mano: un despacho sin fecha pasa el filtro de edad, y metería al mapa avisos
  de hace días con la hora de hoy.

**Por qué este Actor.** El anterior (`apidojo/tweet-scraper`) deja de raspar en
el plan Free cuando lo lanza el Scheduler: terminaba en `SUCCEEDED` con items
de relleno, y el webhook los contaba como tuits sin clave. De ahí la regla de
ceguera de arriba.

## `configurar.ps1`

Crea o actualiza los dos Tasks, los **prueba** con una corrida por la API y
recién entonces ajusta Schedules y webhooks. Lee `APIFY_TOKEN` y
`APIFY_WEBHOOK_SECRET` del entorno o de `backend\.env`.

```powershell
.\infra\apify\configurar.ps1             # aplica
.\infra\apify\configurar.ps1 -DryRun     # muestra qué haría, sin tocar nada
.\infra\apify\configurar.ps1 -Auditar    # webhooks, Tasks, Schedules, consumo y lo que trajeron las últimas corridas
.\infra\apify\configurar.ps1 -Cron "0 */2 * * *"   # otra cadencia (ver «Costo»)
.\infra\apify\configurar.ps1 -SinProbar  # sin la corrida de prueba (no recomendado)
```

**Orden:** primero el Task, después el Schedule y al final los webhooks. El
Schedule es lo que gasta crédito, y con la cuenta al tope Apify rechaza crear
webhooks: un fallo de webhook o de actualización del Task queda como aviso al
final y no detiene el resto. La prueba usa la API y no el Scheduler: después
del primer disparo programado hay que mirar `-Auditar` o `/collectors/health`.

Al final imprime las variables de Render:

```
APIFY_BOMBEROS_ACTOR_IDS = <id de alertav-bomberos>,<id de alertav-canario>
APIFY_X_CANARIO_IDS      = <id de alertav-canario>
APIFY_X_SCHEDULE_MINUTES = 30
```

Mientras `APIFY_BOMBEROS_ACTOR_IDS` esté vacía el filtro está apagado y
cualquier corrida puede entregar. **Al cambiar de Actor el Task es otro y su id
también**: hay que reemplazarlo en Render, o el webhook ignora las entregas.

## Costo

Plan Free: US$ 5 al mes, período del 26 al 25. Con `queryType: Latest` cada
corrida devuelve los tuits más recientes **aunque ya se hayan leído**, y la
ventana es lo que evita volver a pagarlos. Peor caso del mes, con la plataforma
incluida:

| Task | Corridas al mes | Tuits por corrida (máx.) | Peor caso |
|---|---|---|---|
| `alertav-bomberos` | 1.488 | 17 | US$ 4,09 |
| `alertav-canario` | 31 | 12 | US$ 0,06 |
| **Total** | | | **US$ 4,15** |

Lo normal es mucho menos: el peor caso supone 17 publicaciones cada media hora.
`configurar.ps1` hace esta cuenta antes de tocar nada y **se niega a seguir** si
pasa de US$ 4,50, salvo con `-AceptarCosto` (para un plan de pago).

Lo que se pierde: si una central publica más de 6 cosas en 45 minutos, las más
viejas no llegan. En un incendio grande casi todas son «SALE … A …», que se
anexan igual al original si llegan. `maxItemsPerTarget` reparte el cupo, así que
una central muy activa no deja fuera a las demás.

Si se cambia el Cron, `APIFY_X_SCHEDULE_MINUTES` en Render tiene que tener el
mismo valor: si no, `/collectors/health` marca el webhook como detenido entre
dos entregas normales. `APIFY_WEBHOOK_MAX_AGE_MINUTES` (180) tiene que seguir
siendo mayor que la cadencia.

### Run options

```
Timeout             180 s      (0 = sin límite: una corrida colgada se come el crédito)
Memory              512 MB     (con 256 MB va lento y muere por timeout)
Maximum cost/run    US$ 0,01   (maxTotalChargeUsd; -MaxCostoPorCorrida en el script;
                               una corrida normal cuesta ~US$ 0,002)
```

## Webhooks: sólo en el Task, nunca en el Actor

Un webhook colgado del Actor dispara también para las corridas que lanza un Task
suyo. Si está en los dos niveles, cada corrida entrega dos veces: no duplica
eventos —`external_id` es determinista— pero duplica llamadas al modelo y hace
competir dos lecturas por el presupuesto de geocodificación.

Cabecera: `X-AlertaV-Apify-Secret: <APIFY_WEBHOOK_SECRET>`. Evento:
`ACTOR.RUN.SUCCEEDED`. La plantilla de payload por defecto sirve tal cual.

## Claves de Bomberos

Cada Cuerpo tiene su propio sistema de claves (`vocabulary.SistemaClaves`). El
backend lee el autor de cada tuit (`author.userName` o la URL) y usa la tabla
de su Cuerpo. Un tuit de una cuenta **sin tabla** no se ingiere, aunque venga en
el mismo dataset. Tampoco los retuits.

| | @CGI_CBV (Valparaíso) | @CBVM132 (Viña del Mar y Concón) |
|---|---|---|
| Formato | `72 * PRIMERO DE MAYO / 12 DE OCTUBRE * CLAVE 4-1` | `Clave 5-1 LOS PELLINES / LOS GINKOS U-63, U-82` |
| `Clave 3` | no existe (3-1 / 3-2) | incendio vehicular |
| `Clave 10` | abastecer agua: **no se ingiere** | otros servicios: **se ingiere** |
| `Clave 16` | no existe | servicios internos: no se ingiere |
| Claves que entran | `BOMBEROS_ACCIDENT_KEYS` | `BOMBEROS_CBVM_KEYS` |

| | @despachoscbla (Los Andes) | @CBQuilpue y @cbquillota |
|---|---|---|
| Formato | `10-0-1 (LLAMADO ESTRUCTURAL), MEMBRILLAR /CHACABUCO, QB-2,RB-3,BT-5` | `Clave 5-1 AYMARAS / LOS CARRERA M-43, M-11` (como Viña) |
| Sistema | **nacional**: las emergencias son la familia 10; de 0 a 9 es radio. El `0` de `10-0-x` NO se colapsa | la familia 1–6 de la costa |
| Tabla | Wurtlitzer, completa | **provisional**: no hay tabla publicada |
| No se ingiere | radio, 10-9, 10-10, 10-11, 10-12, 10-15 y algunos subtipos | 17-x de Quilpué (servicios internos), 15 de Quillota |
| Claves que entran | `BOMBEROS_CBLA_KEYS` | `BOMBEROS_QUILPUE_KEYS`, `BOMBEROS_QUILLOTA_KEYS` |

En el CBV, `10` es abastecer agua y `12` es **Academia de Cuerpo** (una
capacitación); los accidentes son `5-x` (rescate vehicular) y `9-x` (túnel).
`NON_INCIDENT_CODES` declara lo que se despacha y no es una emergencia del mapa
(7, 8, 10, 11, 12, 13, 14). La `Clave 14` del CBVM queda a propósito sin
decidir: una versión de la tabla la da como accidente eléctrico y otra como
ejercicio de unidad.

El prompt de Gemini se construye **desde** la tabla de cada Cuerpo en tiempo de
ejecución: no hay un glosario escrito a mano que pueda quedar atrás. Si una
central despacha una clave que no está, queda avisada en el log y en
`collector_runs` (la corrida queda `partial`):

```
la central publicó claves que no están en BOMBEROS_ACCIDENT_KEYS
  claves: {"7-9": 1, "CBVM Clave 14": 2}
```

Antes de agregarla hay que saber qué significa: sin entrada en la tabla del
Cuerpo el despacho entra sin tipo, y uno de peso 1.00 mal tipificado es peor
que uno perdido. Hay tests que lo impiden en los dos sentidos.
