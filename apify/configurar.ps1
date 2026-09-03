# Crea en Apify los tres Tasks, sus webhooks y el Schedule.
#
# Es idempotente: busca por nombre antes de crear, y actualiza si ya existe. Se
# puede correr las veces que haga falta.
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

param(
    [switch]$DryRun,
    [switch]$Auditar,
    [string]$ApiBase = "https://api.apify.com/v2",
    [string]$BackendUrl = "https://alertav-api.onrender.com",
    [string]$Cron = "*/30 * * * *"
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
responderia 401 a cada entrega — hasta que Apify deshabilite la integracion.

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


# --- Definicion de los tres Tasks -------------------------------------------

$tasks = @(
    @{ nombre = "alertav-bomberos";  actor = "apidojo~tweet-scraper";
       archivo = "task-bomberos.json";  webhook = "/api/v1/apify/webhook" }
    @{ nombre = "alertav-prensa";    actor = "apidojo~tweet-scraper";
       archivo = "task-prensa.json";    webhook = "/api/v1/apify/webhook/prensa" }
    # Instagram NO lleva webhook: el collector es *pull* y lee
    # runs/last?status=SUCCEEDED por su cuenta cada 5 min.
    @{ nombre = "alertav-instagram"; actor = "apify~instagram-scraper";
       archivo = "task-instagram.json"; webhook = $null }
)

# Timeout 180s y no 0 (=sin limite): una corrida colgada se come el credito.
# Memory 512MB y no 256: con menos va lento, y lento choca con el timeout — y
# una corrida que expira NO queda en SUCCEEDED, que es lo que el collector lee.
$runOptions = @{ build = "latest"; timeoutSecs = 180; memoryMbytes = 512 }

$resultado = @()

foreach ($t in $tasks) {
    $input = Get-Content (Join-Path $PSScriptRoot $t.archivo) -Raw -Encoding UTF8 | ConvertFrom-Json

    # ¿Existe ya? Se busca por nombre para poder re-ejecutar sin duplicar.
    $existentes = (Api GET "/actor-tasks?limit=1000").data.items
    $previo = $existentes | Where-Object { $_.name -eq $t.nombre } | Select-Object -First 1

    if ($DryRun) {
        $accion = if ($previo) { "ACTUALIZARIA" } else { "CREARIA" }
        Write-Host "$accion task $($t.nombre) sobre $($t.actor)"
        continue
    }

    if ($previo) {
        $task = Api PUT "/actor-tasks/$($previo.id)" @{
            name = $t.nombre; options = $runOptions; input = $input
        }
        Write-Host "Task actualizado: $($t.nombre)" -ForegroundColor Cyan
    }
    else {
        $actor = Api GET "/acts/$($t.actor)"
        $task = Api POST "/actor-tasks" @{
            actId = $actor.data.id; name = $t.nombre
            options = $runOptions; input = $input
        }
        Write-Host "Task creado: $($t.nombre)" -ForegroundColor Green
    }

    $taskId = $task.data.id

    # --- Webhook, SOLO sobre el Task ----------------------------------------
    #
    # Nunca sobre el Actor: un webhook colgado del Actor dispara tambien para
    # las corridas de sus Tasks, y con los dos puestos cada corrida entrega dos
    # veces.
    if ($t.webhook) {
        $url = "$BackendUrl$($t.webhook)"
        $plantilla = if ($secreto) {
            (@{ "X-AlertaV-Apify-Secret" = $secreto } | ConvertTo-Json -Compress)
        } else { "{}" }

        $wh = (Api GET "/webhooks?limit=1000").data.items |
              Where-Object { $_.condition.actorTaskId -eq $taskId } |
              Select-Object -First 1

        $cuerpo = @{
            eventTypes      = @("ACTOR.RUN.SUCCEEDED")
            condition       = @{ actorTaskId = $taskId }
            requestUrl      = $url
            headersTemplate = $plantilla
            isAdHoc         = $false
        }

        if ($wh) {
            Api PUT "/webhooks/$($wh.id)" $cuerpo | Out-Null
            Write-Host "  webhook actualizado -> $($t.webhook)" -ForegroundColor Cyan
        } else {
            Api POST "/webhooks" $cuerpo | Out-Null
            Write-Host "  webhook creado      -> $($t.webhook)" -ForegroundColor Green
        }

        # --- Comprobar la cabecera contra el backend REAL --------------------
        #
        # El script termina diciendo "listo" aunque haya dejado una cabecera que
        # el backend rechaza. Sin esta comprobacion, el unico aviso llega por
        # correo de Apify horas despues —"Endpoint responded with HTTP status
        # code 401"— y para entonces la integracion puede estar deshabilitada.
        #
        # Se manda un cuerpo sin `defaultDatasetId` a proposito: el backend lo
        # responde `200 ignored` sin leer ningun dataset ni escribir en la base.
        # Lo unico que se esta probando es la puerta.
        try {
            $sonda = Invoke-WebRequest -Method POST -Uri $url `
                -Headers @{ "X-AlertaV-Apify-Secret" = $secreto } `
                -Body '{"eventType":"CONFIGURACION","resource":{}}' `
                -ContentType "application/json" -UseBasicParsing -TimeoutSec 45
            Write-Host "  verificado          -> HTTP $($sonda.StatusCode)" -ForegroundColor DarkGray
        } catch {
            $codigo = $_.Exception.Response.StatusCode.value__
            if ($codigo -eq 401) {
                throw @"
El backend rechazo la cabecera con 401 en $url

El webhook quedo creado pero NO va a funcionar: el valor de
APIFY_WEBHOOK_SECRET que se uso aca no coincide con el de Render.
Corregilo y volve a correr este script — es idempotente.
"@
            }
            Write-Host "  AVISO: la sonda devolvio HTTP $codigo (revisar)" -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "  sin webhook (pull)" -ForegroundColor DarkGray
    }

    $resultado += [pscustomobject]@{ Task = $t.nombre; TaskId = $taskId }
}

if ($DryRun) { return }

# --- Limpieza de webhooks huerfanos -----------------------------------------
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

# --- Tasks duplicados: se reportan, NO se borran -----------------------------
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
    Write-Host "  Sus webhooks ya se eliminaron, asi que no entregan nada." -ForegroundColor DarkGray
    Write-Host "  Si son de la configuracion manual anterior, borralos en el panel" -ForegroundColor DarkGray
    Write-Host "  y sacalos del Schedule para no gastar credito." -ForegroundColor DarkGray
}

# --- Schedule unico con los tres Tasks --------------------------------------
#
# 30 minutos y no 5: APIFY_MAX_RUN_AGE_MINUTES tolera 45, asi que media hora
# deja margen para una corrida fallida sin que la capa se declare ciega, y
# estira el credito del plan gratuito.

$acciones = $resultado | ForEach-Object {
    @{ type = "RUN_ACTOR_TASK"; actorTaskId = $_.TaskId }
}

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
    Write-Host "`nSchedule 'alertav' actualizado ($Cron)" -ForegroundColor Cyan
} else {
    Api POST "/schedules" $cuerpoSchedule | Out-Null
    Write-Host "`nSchedule 'alertav' creado ($Cron)" -ForegroundColor Green
}

# --- Lo que hay que copiar a Render -----------------------------------------
#
# Los dos Tasks salen del mismo Actor y comparten actId, asi que el guard tiene
# que autorizar el actorTaskId. Estos ids NO son secretos: identifican un Task,
# no autorizan nada por si solos.

Write-Host "`n--- Variables para Render ---" -ForegroundColor Yellow
foreach ($r in $resultado) {
    switch ($r.Task) {
        "alertav-bomberos" { Write-Host "APIFY_BOMBEROS_ACTOR_IDS = $($r.TaskId)" }
        "alertav-prensa"   { Write-Host "APIFY_PRENSA_ACTOR_IDS   = $($r.TaskId)" }
    }
}
Write-Host "`nListo." -ForegroundColor Green
