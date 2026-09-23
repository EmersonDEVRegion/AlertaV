# Crea en Apify el Task de X de las centrales de Bomberos, su webhook y el
# Schedule.
#
# DESDE EL 2026-09-22 ES UN SOLO TASK. Los de prensa (X) y de Instagram salieron
# por cuota: el plan gratuito no alcanzaba para tres, y lo que cubrian lo toma
# la prensa local por RSS en el backend. Correr este script deja el Schedule
# 'alertav' con el Task de Bomberos solo, borra el webhook huerfano del Task de
# prensa y LISTA los dos Tasks viejos para que los borres en el panel (no los
# borra: ver "Tasks duplicados" mas abajo).
#
# Es idempotente: busca por nombre antes de crear, y actualiza si ya existe. Se
# puede correr las veces que haga falta.
#
# EL ORDEN IMPORTA (2026-09-23)
# -----------------------------
# Primero el Task, despues el Schedule y recien despues los webhooks. El
# Schedule es lo que gasta credito, y con la cuenta al tope Apify rechaza crear
# webhooks: si el webhook iba antes y fallaba, el script abortaba y el Schedule
# seguia corriendo los tres Tasks viejos. Un fallo de webhook ahora se avisa y
# no detiene nada.
#
# SOBRE LAS CREDENCIALES
# ----------------------
# `APIFY_TOKEN` y `APIFY_WEBHOOK_SECRET` se leen de backend\.env y NUNCA se
# imprimen ni se escriben en ningún archivo. El token viaja en la cabecera
# `Authorization`, jamás en la query — mismo invariante que el backend: una URL
# con el token dentro termina en los logs del proxy y en el historial de quien
# la copie.
#
#   .\apify\configurar.ps1
#   .\apify\configurar.ps1 -DryRun     # muestra qué haría, sin tocar nada
#   .\apify\configurar.ps1 -Auditar    # lista webhooks, tasks, schedules y consumo
#   .\apify\configurar.ps1 -Cron "0 */2 * * *"   # otra cadencia (ver COSTO)

param(
    [switch]$DryRun,
    [switch]$Auditar,
    # Permite una cadencia cuyo peor caso supera el plan gratuito (ver COSTO).
    [switch]$AceptarCosto,
    [string]$ApiBase = "https://api.apify.com/v2",
    [string]$BackendUrl = "https://alertav-api.onrender.com",
    # Cada hora, no cada 30 minutos: ver COSTO. Si se cambia, APIFY_X_SCHEDULE_MINUTES
    # en Render tiene que cambiar con el (el script lo imprime al final).
    [string]$Cron = "0 * * * *",
    # Tope de gasto de UNA corrida (opcion maxTotalChargeUsd del Task). Con
    # maxItems = 15 y US$ 0,0004 por tuit, una corrida normal cuesta US$ 0,006.
    [double]$MaxCostoPorCorrida = 0.01
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot

# --- Credenciales ------------------------------------------------------------

function Read-EnvValue([string]$archivo, [string]$clave) {
    if (-not (Test-Path $archivo)) { throw "No existe $archivo" }
    foreach ($linea in Get-Content $archivo) {
        if ($linea -match "^\s*$clave\s*=\s*(.*)$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return ""
}

# Primero el entorno del proceso, despues backend\.env.
#
# Ese orden importa en este proyecto: las credenciales de produccion viven en
# Render, no en el disco, y el .env local puede estar meses atrasado — lo estaba
# la primera vez que se corrio esto. Con el entorno primero, se puede pasar la
# credencial para una sola invocacion sin escribirla en ningun archivo.
function Get-Credencial([string]$clave) {
    $delEntorno = [Environment]::GetEnvironmentVariable($clave)
    if ($delEntorno) { return $delEntorno.Trim() }
    return Read-EnvValue (Join-Path $raiz "backend\.env") $clave
}

$token = Get-Credencial "APIFY_TOKEN"
$secreto = Get-Credencial "APIFY_WEBHOOK_SECRET"

if (-not $token) {
    throw @"
APIFY_TOKEN no esta ni en el entorno ni en backend\.env.

Sacalo de https://console.apify.com/settings/integrations y pasalo asi, en la
MISMA linea que la llamada para que no quede en tu historial de sesion:

    `$env:APIFY_TOKEN='apify_api_...'; `$env:APIFY_WEBHOOK_SECRET='...'; .\apify\configurar.ps1

O ponelos en backend\.env, que esta en .gitignore.
"@
}
Write-Host "Token cargado ($($token.Length) caracteres)." -ForegroundColor DarkGray

# Sin secreto NO se crean webhooks. Antes esto era un aviso amarillo y se
# seguía adelante; el resultado fue dos integraciones que no podian funcionar
# ni una sola vez.
#
# Si el backend tiene APIFY_WEBHOOK_SECRET puesto —y en produccion lo tiene— la
# ruta responde 401 a toda entrega sin cabecera. Apify reintenta once veces y
# despues DESHABILITA la integracion. O sea que crear el webhook sin secreto no
# deja el sistema "abierto pero andando": lo deja roto, y encima el diagnostico
# llega por correo horas mas tarde.
#
# El 2026-09-03 costo exactamente eso: dos correos de Apify diciendo
# "Endpoint responded with HTTP status code 401", uno por Task.
if (-not $secreto) {
    throw @"
APIFY_WEBHOOK_SECRET no esta ni en el entorno ni en backend\.env.

Sin el, los webhooks se crearian sin cabecera de autenticacion y el backend
responderia 401 a cada entrega, hasta que Apify deshabilite la integracion.

Pasalo junto al token, en la misma linea:

    `$env:APIFY_TOKEN='apify_api_...'; `$env:APIFY_WEBHOOK_SECRET='...'; .\apify\configurar.ps1

Tiene que ser EL MISMO valor que la variable APIFY_WEBHOOK_SECRET de Render.
"@
}

$headers = @{ Authorization = "Bearer $token" }

function Api([string]$metodo, [string]$ruta, $cuerpo = $null) {
    $uri = "$ApiBase$ruta"
    if ($cuerpo) {
        $json = $cuerpo | ConvertTo-Json -Depth 12 -Compress
        return Invoke-RestMethod -Method $metodo -Uri $uri -Headers $headers `
            -ContentType "application/json; charset=utf-8" `
            -Body ([System.Text.Encoding]::UTF8.GetBytes($json))
    }
    return Invoke-RestMethod -Method $metodo -Uri $uri -Headers $headers
}

# --- Consumo de la cuenta -----------------------------------------------------
#
# El 2026-09-03 la cuenta llego a US$ 5,01 de US$ 5 y Apify dejo de iniciar
# Actors hasta el periodo siguiente. Nadie lo vio hasta el correo: el mapa solo
# mostro que Bomberos dejaba de entregar. Se imprime siempre, al principio.
#
# Lo que se gasto ese periodo (Billing, 2026-09-23):
#   apify/instagram-scraper   1.682 resultados x US$ 0,0027 = US$ 4,54
#   apidojo/tweet-scraper     1.120 tuits      x US$ 0,0004 = US$ 0,45
# Instagram fue el 91 %. Por eso salio.

function Show-Consumo {
    try {
        $lim = (Api GET "/users/me/limits").data
        $usado = [double]$lim.current.monthlyUsageUsd
        $tope = [double]$lim.limits.maxMonthlyUsageUsd
        $fin = $lim.monthlyUsageCycle.endAt
        Write-Host ("Consumo del periodo: US$ {0:N2} de US$ {1:N2} (el periodo termina {2})" -f $usado, $tope, $fin) -ForegroundColor DarkGray
        if ($tope -gt 0 -and $usado -ge $tope) {
            Write-Host "  CUENTA AL TOPE: Apify no inicia corridas hasta el proximo periodo." -ForegroundColor Yellow
            Write-Host "  El Schedule se aplica igual, para que al renovarse corra SOLO Bomberos." -ForegroundColor Yellow
        }
    }
    catch {
        Write-Host "  (no se pudo leer el consumo: $($_.Exception.Message))" -ForegroundColor DarkGray
    }
}

# --- COSTO: cuanto cuesta la cadencia elegida -----------------------------------
#
# El Actor de X (apidojo/tweet-scraper) cobra US$ 0,0004 por tuit devuelto, y
# con sort=Latest cada corrida devuelve los `maxItems` tuits mas recientes, SEAN
# NUEVOS O NO: los repetidos se vuelven a cobrar. El peor caso del mes es
#
#     corridas al mes x maxItems x US$ 0,0004
#
#   cada 30 min, 25 tuits:  1.488 x 25 x 0,0004 = US$ 14,88   (no cabe en Free)
#   cada 1 h,    15 tuits:    744 x 15 x 0,0004 = US$  4,46   (cabe, con margen)
#   cada 2 h,    25 tuits:    372 x 25 x 0,0004 = US$  3,72
#
# El plan Free tiene US$ 5 al mes y ademas cobra storage y transferencia (unos
# centavos). Si el peor caso pasa de US$ 4,50 el script se niega a seguir sin
# -AceptarCosto, que es para cuando la cuenta tenga un plan de pago.
#
# Lo que se pierde con 15 tuits por hora: si las dos centrales publican mas de
# 15 despachos en una hora, los mas viejos de esa hora no llegan. Si el log del
# webhook muestra corridas con exactamente 15 items una y otra vez, esa es la
# senal de que hace falta mas cupo (plan de pago o mas frecuencia).

$PrecioPorTuit = 0.0004
$TopePlanFree = 4.5

# Corridas al mes de un cron simple: "M * * * *", "*/N * * * *", "0 */N * * *",
# listas con comas. Devuelve $null si el cron es de otra forma (dia, mes o dia de
# la semana restringidos): en ese caso no se estima y se avisa.
function Get-CorridasPorDia([string]$cron) {
    $p = $cron.Trim() -split '\s+'
    if ($p.Count -ne 5) { return $null }
    if ($p[2] -ne '*' -or $p[3] -ne '*' -or $p[4] -ne '*') { return $null }

    $porHora = $null
    if ($p[0] -match '^\*/(\d+)$') { $porHora = [math]::Ceiling(60 / [int]$Matches[1]) }
    elseif ($p[0] -match '^\d+(,\d+)*$') { $porHora = ($p[0] -split ',').Count }
    if ($null -eq $porHora) { return $null }

    $horas = $null
    if ($p[1] -eq '*') { $horas = 24 }
    elseif ($p[1] -match '^\*/(\d+)$') { $horas = [math]::Ceiling(24 / [int]$Matches[1]) }
    elseif ($p[1] -match '^\d+(,\d+)*$') { $horas = ($p[1] -split ',').Count }
    if ($null -eq $horas) { return $null }

    return $porHora * $horas
}

Show-Consumo

# --- Auditoria: solo lee, no cambia nada ------------------------------------
#
# Existe porque despues de limpiar seguian llegando correos de 401 nombrando un
# Task viejo, y sin ver el estado completo de la cuenta cualquier explicacion
# era conjetura. Muestra las TRES cosas que pueden disparar una entrega:
# webhooks, tasks y schedules. El script gestiona un solo schedule —`alertav`—
# asi que cualquier otro que exista sigue corriendo lo que tenga dentro, y eso
# no se ve mirando webhooks.

if ($Auditar) {
    $anfitrion = ([uri]$BackendUrl).Host

    Write-Host "`n=== WEBHOOKS que apuntan a $anfitrion ===" -ForegroundColor Cyan
    $webhooks = (Api GET "/webhooks?limit=1000").data.items |
                Where-Object { $_.requestUrl -and ([uri]$_.requestUrl).Host -eq $anfitrion }
    if (-not $webhooks) { Write-Host "  (ninguno)" }
    foreach ($w in $webhooks) {
        $de = if ($w.condition.actorTaskId) { "task $($w.condition.actorTaskId)" }
              elseif ($w.condition.actorId) { "ACTOR $($w.condition.actorId)" }
              else { "sin condicion" }
        # `headersTemplate` puede traer el secreto: se dice si LO HAY, nunca cual.
        $cab = if ($w.headersTemplate -and $w.headersTemplate -match "Secret") { "con cabecera" }
               else { "SIN CABECERA -> 401 seguro" }
        Write-Host "  [$($w.id)] $de"
        Write-Host "      $($w.requestUrl)  ($cab)"
    }

    Write-Host "`n=== TASKS con nombre de AlertaV ===" -ForegroundColor Cyan
    $tareas = (Api GET "/actor-tasks?limit=1000").data.items |
              Where-Object { $_.name -match "(?i)alerta" }
    foreach ($t in $tareas) { Write-Host "  [$($t.id)] $($t.name)" }

    Write-Host "`n=== SCHEDULES ===" -ForegroundColor Cyan
    $nombres = @{}; foreach ($t in $tareas) { $nombres[$t.id] = $t.name }
    foreach ($s in (Api GET "/schedules?limit=1000").data.items) {
        $estado = if ($s.isEnabled) { "activo" } else { "pausado" }
        Write-Host "  '$($s.name)' ($($s.cronExpression)) $estado"
        foreach ($a in $s.actions) {
            $quien = if ($nombres.ContainsKey($a.actorTaskId)) { $nombres[$a.actorTaskId] }
                     else { $a.actorTaskId }
            Write-Host "      -> $quien"
        }
    }
    Write-Host ""
    return
}


# --- Definicion del Task ----------------------------------------------------
#
# Uno solo: las dos centrales (@CGI_CBV y @CBVM132) en la misma corrida. El
# backend separa cada tuit por su autor y lo lee con el diccionario de claves
# de su Cuerpo.
#
# Los Tasks retirados, por si hubiera que volver a encenderlos (junto con
# APIFY_PRENSA_ENABLED / APIFY_INSTAGRAM_ENABLED en Render):
#
#   @{ nombre = "alertav-prensa";    actor = "apidojo~tweet-scraper";
#      archivo = "task-prensa.json";    webhook = "/api/v1/apify/webhook/prensa" }
#   @{ nombre = "alertav-instagram"; actor = "apify~instagram-scraper";
#      archivo = "task-instagram.json"; webhook = $null }

$tasks = @(
    @{ nombre = "alertav-bomberos";  actor = "apidojo~tweet-scraper";
       archivo = "task-bomberos.json";  webhook = "/api/v1/apify/webhook" }
)

# Timeout 180s y no 0 (=sin limite): una corrida colgada se come el credito.
# Memory 512MB y no 256: con menos va lento, y lento choca con el timeout — y
# una corrida que expira NO queda en SUCCEEDED, que es lo que el collector lee.
# maxTotalChargeUsd: el tope de gasto por corrida que el README prometia y el
# Task no tenia ("Maximum cost per run: Unlimited" en el panel, 2026-09-23).
$runOptions = @{
    build             = "latest"
    timeoutSecs       = 180
    memoryMbytes      = 512
    maxTotalChargeUsd = $MaxCostoPorCorrida
}

# --- Estimacion de costo, antes de tocar nada ---------------------------------

$corridasDia = Get-CorridasPorDia $Cron
$maxItems = 0
foreach ($t in $tasks) {
    $e = Get-Content (Join-Path $PSScriptRoot $t.archivo) -Raw -Encoding UTF8 | ConvertFrom-Json
    $maxItems += [int]$e.maxItems
}
$cadenciaMin = $null
if ($null -eq $corridasDia) {
    Write-Host "No se puede estimar el costo del cron '$Cron' (forma no simple). Revisalo a mano." -ForegroundColor Yellow
}
else {
    $cadenciaMin = [math]::Ceiling(1440 / $corridasDia)
    $peorCaso = $corridasDia * 31 * $maxItems * $PrecioPorTuit
    Write-Host ("Cadencia '{0}': {1} corridas al dia, hasta {2} tuits cada una -> peor caso US$ {3:N2} al mes" -f $Cron, $corridasDia, $maxItems, $peorCaso)
    if ($peorCaso -gt $TopePlanFree -and -not $AceptarCosto) {
        throw @"
El peor caso (US$ $([math]::Round($peorCaso, 2)) al mes) no cabe en el plan gratuito de Apify.

Con la cuenta en Free, al pasar los US$ 5 Apify detiene TODOS los Actors hasta
el periodo siguiente, y Bomberos deja de entregar por dias. Opciones:
  - una cadencia mas espaciada:  -Cron "0 */2 * * *"
  - menos tuits por corrida: bajar maxItems en task-bomberos.json
  - si la cuenta ya tiene plan de pago, repetir con -AceptarCosto
"@
    }
}

$resultado = @()
$avisos = @()

# --- 1. El Task ---------------------------------------------------------------

foreach ($t in $tasks) {
    $entrada = Get-Content (Join-Path $PSScriptRoot $t.archivo) -Raw -Encoding UTF8 | ConvertFrom-Json

    # ¿Existe ya? Se busca por nombre para poder re-ejecutar sin duplicar.
    $existentes = (Api GET "/actor-tasks?limit=1000").data.items
    $previo = $existentes | Where-Object { $_.name -eq $t.nombre } | Select-Object -First 1

    if ($DryRun) {
        $accion = if ($previo) { "ACTUALIZARIA" } else { "CREARIA" }
        Write-Host "$accion task $($t.nombre) sobre $($t.actor)"
        continue
    }

    if ($previo) {
        # Si Apify rechaza la actualizacion (cuenta al tope, por ejemplo), el
        # Task viejo sigue existiendo y el Schedule se ajusta igual con su id:
        # que deje de correr lo retirado importa mas que actualizar el input.
        try {
            $task = Api PUT "/actor-tasks/$($previo.id)" @{
                name = $t.nombre; options = $runOptions; input = $entrada
            }
            $taskId = $task.data.id
            Write-Host "Task actualizado: $($t.nombre)" -ForegroundColor Cyan
        }
        catch {
            $taskId = $previo.id
            $avisos += "task $($t.nombre): $($_.Exception.Message)"
            Write-Host "  AVISO: no se pudo actualizar el Task $($t.nombre); el Schedule se ajusta igual." -ForegroundColor Yellow
        }
    }
    else {
        $actor = Api GET "/acts/$($t.actor)"
        $task = Api POST "/actor-tasks" @{
            actId = $actor.data.id; name = $t.nombre
            options = $runOptions; input = $entrada
        }
        $taskId = $task.data.id
        Write-Host "Task creado: $($t.nombre)" -ForegroundColor Green
    }

    $resultado += [pscustomobject]@{
        Task = $t.nombre; TaskId = $taskId; Webhook = $t.webhook
    }
}

if ($DryRun) {
    Write-Host "Dejaria el Schedule 'alertav' con: $(($tasks | ForEach-Object { $_.nombre }) -join ', ') ($Cron)"
    return
}

# --- 2. El Schedule, antes que los webhooks -----------------------------------
#
# Es lo que gasta credito, y por eso va primero: ver "EL ORDEN IMPORTA" arriba.
#
# Cada corrida del Actor de X se cobra. Si se cambia el Cron, hay que cambiar
# tambien APIFY_X_SCHEDULE_MINUTES en Render (se imprime al final): con eso la
# salud sabe cada cuanto esperar una entrega y no marca en falso las tres
# familias. Y APIFY_WEBHOOK_MAX_AGE_MINUTES (180) tiene que seguir siendo MAYOR
# que la cadencia, o un despacho publicado justo despues de una corrida llega
# viejo a la siguiente y se descarta.

$acciones = @($resultado | ForEach-Object {
    @{ type = "RUN_ACTOR_TASK"; actorTaskId = $_.TaskId }
})

$previo = (Api GET "/schedules?limit=1000").data.items |
          Where-Object { $_.name -eq "alertav" } | Select-Object -First 1

$cuerpoSchedule = @{
    name           = "alertav"
    cronExpression = $Cron
    isEnabled      = $true
    isExclusive    = $true
    timezone       = "America/Santiago"
    actions        = $acciones
}

if ($previo) {
    Api PUT "/schedules/$($previo.id)" $cuerpoSchedule | Out-Null
    Write-Host "Schedule 'alertav' actualizado ($Cron): solo $(($resultado | ForEach-Object { $_.Task }) -join ', ')" -ForegroundColor Cyan
} else {
    Api POST "/schedules" $cuerpoSchedule | Out-Null
    Write-Host "Schedule 'alertav' creado ($Cron)" -ForegroundColor Green
}

# --- 3. Webhooks, SOLO sobre el Task ------------------------------------------
#
# Nunca sobre el Actor: un webhook colgado del Actor dispara tambien para las
# corridas de sus Tasks, y con los dos puestos cada corrida entrega dos veces.
#
# Un fallo aca NO detiene el script: el Task y el Schedule ya quedaron bien, y
# con la cuenta al tope Apify responde que los webhooks no estan habilitados.
# Se avisa y se sigue. La unica excepcion es el 401 de la sonda, que significa
# que el secreto no coincide con el de Render y el webhook no va a servir.

foreach ($r in $resultado) {
    if (-not $r.Webhook) {
        Write-Host "  $($r.Task): sin webhook (pull)" -ForegroundColor DarkGray
        continue
    }

    $url = "$BackendUrl$($r.Webhook)"
    $plantilla = (@{ "X-AlertaV-Apify-Secret" = $secreto } | ConvertTo-Json -Compress)
    $cuerpo = @{
        eventTypes      = @("ACTOR.RUN.SUCCEEDED")
        condition       = @{ actorTaskId = $r.TaskId }
        requestUrl      = $url
        headersTemplate = $plantilla
        isAdHoc         = $false
    }

    try {
        $wh = (Api GET "/webhooks?limit=1000").data.items |
              Where-Object { $_.condition.actorTaskId -eq $r.TaskId } |
              Select-Object -First 1
        if ($wh) {
            Api PUT "/webhooks/$($wh.id)" $cuerpo | Out-Null
            Write-Host "  webhook actualizado -> $($r.Webhook)" -ForegroundColor Cyan
        } else {
            Api POST "/webhooks" $cuerpo | Out-Null
            Write-Host "  webhook creado      -> $($r.Webhook)" -ForegroundColor Green
        }
    }
    catch {
        $avisos += "webhook de $($r.Task): $($_.Exception.Message)"
        Write-Host "  AVISO: no se pudo crear/actualizar el webhook de $($r.Task)." -ForegroundColor Yellow
        Write-Host "  Si la cuenta esta al tope, repetir el script cuando se renueve el periodo." -ForegroundColor Yellow
        continue
    }

    # --- Comprobar la cabecera contra el backend REAL ------------------------
    #
    # El script termina diciendo "listo" aunque haya dejado una cabecera que el
    # backend rechaza. Sin esta comprobacion, el unico aviso llega por correo de
    # Apify horas despues —"Endpoint responded with HTTP status code 401"— y
    # para entonces la integracion puede estar deshabilitada.
    #
    # Se manda un cuerpo sin `defaultDatasetId` a proposito: el backend lo
    # responde `200 ignored` sin leer ningun dataset ni escribir en la base. Lo
    # unico que se esta probando es la puerta.
    #
    # 90 s y no 45: en el plan gratuito de Render el servicio se duerme y la
    # primera peticion tarda en despertarlo.
    try {
        $sonda = Invoke-WebRequest -Method POST -Uri $url `
            -Headers @{ "X-AlertaV-Apify-Secret" = $secreto } `
            -Body '{"eventType":"CONFIGURACION","resource":{}}' `
            -ContentType "application/json" -UseBasicParsing -TimeoutSec 90
        Write-Host "  verificado          -> HTTP $($sonda.StatusCode)" -ForegroundColor DarkGray
    } catch {
        $codigo = $_.Exception.Response.StatusCode.value__
        if ($codigo -eq 401) {
            throw @"
El backend rechazo la cabecera con 401 en $url

El webhook quedo creado pero NO va a funcionar: el valor de
APIFY_WEBHOOK_SECRET que se uso aca no coincide con el de Render.
Corregilo y volve a correr este script: es idempotente.
"@
        }
        $avisos += "sonda de $($r.Task): HTTP $codigo"
        Write-Host "  AVISO: la sonda devolvio HTTP $codigo (revisar)" -ForegroundColor Yellow
    }
}

# --- 4. Limpieza de webhooks huerfanos -----------------------------------------
#
# Toda configuracion manual anterior sigue viva en el panel. Un webhook viejo
# apuntando a la misma URL entrega igual, con la cabecera que tuviera entonces
# —o sin ninguna— y el backend responde 401 a cada intento. Apify reintenta
# once veces y despues deshabilita esa integracion.
#
# El sintoma son correos de Apify por Tasks que uno cree tener bien
# configurados, y corridas fantasma en `collector_runs`. Ya habia pasado antes
# en este proyecto: el endpoint documenta que baja el log del 401 sin credencial
# a INFO precisamente porque estos huerfanos inundaban Render.
#
# El criterio de borrado es estricto y esta puesto para no pasarse:
#
#   1. Solo webhooks cuya `requestUrl` apunte a ESTE backend. Cualquier otra
#      integracion de la cuenta —Slack, otro proyecto, lo que sea— no se toca.
#   2. Solo los que NO cuelgan de uno de los Tasks que este script gestiona.
#
# Lo segundo cubre tambien los webhooks colgados del ACTOR en vez del Task, que
# son los que provocan la entrega doble: un webhook de Actor dispara ademas para
# las corridas de sus Tasks.

$gestionados = @($resultado | ForEach-Object { $_.TaskId })
$anfitrion = ([uri]$BackendUrl).Host

try {
    $huerfanos = (Api GET "/webhooks?limit=1000").data.items | Where-Object {
        $_.requestUrl -and
        ([uri]$_.requestUrl).Host -eq $anfitrion -and
        $_.condition.actorTaskId -notin $gestionados
    }

    if ($huerfanos) {
        Write-Host "`nWebhooks huerfanos apuntando a $anfitrion :" -ForegroundColor Yellow
        foreach ($h in $huerfanos) {
            $de = if ($h.condition.actorTaskId) { "task $($h.condition.actorTaskId)" }
                  elseif ($h.condition.actorId) { "ACTOR $($h.condition.actorId)" }
                  else { "sin condicion" }
            Write-Host "  borrando: $de -> $($h.requestUrl)"
            Api DELETE "/webhooks/$($h.id)" | Out-Null
        }
        Write-Host "  $($huerfanos.Count) eliminados." -ForegroundColor Green
    }
    else {
        Write-Host "`nSin webhooks huerfanos." -ForegroundColor DarkGray
    }
}
catch {
    $avisos += "limpieza de webhooks: $($_.Exception.Message)"
    Write-Host "`nAVISO: no se pudieron revisar los webhooks huerfanos." -ForegroundColor Yellow
}

# --- 5. Tasks duplicados: se reportan, NO se borran ----------------------------
#
# El script empareja por nombre exacto, asi que una configuracion manual previa
# con otro nombre —"Alertav Prensa" contra `alertav-prensa`— no se reutiliza: se
# crea un Task nuevo al lado. Los dos quedan vivos y, si el viejo esta en algun
# Schedule, sigue corriendo y gastando credito para no entregar nada (su webhook
# acaba de borrarse arriba).
#
# Se reportan y no se borran a proposito. Un Task puede tener un input afinado a
# mano que valga la pena mirar antes de tirarlo, y borrar cosas de la cuenta de
# alguien sin preguntar es de las pocas acciones que no se deshacen.

$otros = (Api GET "/actor-tasks?limit=1000").data.items | Where-Object {
    $_.name -match "(?i)alerta" -and $_.id -notin $gestionados
}

if ($otros) {
    Write-Host "`nTasks con nombre de AlertaV que este script NO gestiona:" -ForegroundColor Yellow
    foreach ($o in $otros) { Write-Host "  $($o.name)  [$($o.id)]" }
    Write-Host "  Sus webhooks ya se eliminaron y el Schedule 'alertav' ya no los corre." -ForegroundColor DarkGray
    Write-Host "  alertav-prensa y alertav-instagram estan RETIRADOS desde 2026-09-22:" -ForegroundColor DarkGray
    Write-Host "  borralos en el panel. Si hay otro Schedule que los corra, sigue" -ForegroundColor DarkGray
    Write-Host "  gastando credito: revisalo con -Auditar." -ForegroundColor DarkGray
}

# --- Lo que hay que copiar a Render -----------------------------------------
#
# El guard tiene que autorizar el actorTaskId. Estos ids NO son secretos:
# identifican un Task, no autorizan nada por si solos.

Write-Host "`n--- Variables para Render ---" -ForegroundColor Yellow
foreach ($r in $resultado) {
    switch ($r.Task) {
        "alertav-bomberos" { Write-Host "APIFY_BOMBEROS_ACTOR_IDS = $($r.TaskId)" }
        "alertav-prensa"   { Write-Host "APIFY_PRENSA_ACTOR_IDS   = $($r.TaskId)" }
    }
}
if ($cadenciaMin) { Write-Host "APIFY_X_SCHEDULE_MINUTES = $cadenciaMin" }

if ($avisos) {
    Write-Host "`nTerminado con avisos:" -ForegroundColor Yellow
    foreach ($a in $avisos) { Write-Host "  - $a" -ForegroundColor Yellow }
}
else {
    Write-Host "`nListo." -ForegroundColor Green
}
