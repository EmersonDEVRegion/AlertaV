# AlertaV — PWA (Fase 4)

Cliente web instalable para el mapa de incidentes de la Región de Valparaíso.
Consume el motor de correlación del backend a través de `/api/v1/incidents/*`.

Stack: **Vite 8 + React 19 + TypeScript 7 + MapLibre GL JS 6 (vía react-map-gl 8)
+ TanStack Query 5 + Tailwind CSS 4 + vite-plugin-pwa**.

---

## Cómo correrlo

Requiere Node 20.19+ o 22.12+ (verificado con Node 22.23).

```bash
cd frontend
cp .env.example .env
npm install
npm run dev            # http://localhost:5173
```

En otra terminal, el backend:

```bash
cd backend
uvicorn app.main:app --reload            # http://localhost:8000
python -m app.services.correlation.runner --loop   # el worker que llena /active
```

Sin el worker corriendo, `/incidents/active` responde `[]` y la PWA muestra el
estado vacío correctamente — no es un error del frontend.

**No hace falta tocar CORS.** El dev server proxea `/api` hacia
`VITE_DEV_API_PROXY` (por defecto `http://localhost:8000`), así que el navegador
nunca cruza de origen y la URL de la API es la misma relativa (`/api/v1`) en
desarrollo y en producción.

Otros comandos:

```bash
npm run typecheck   # tsc --noEmit
npm run lint        # oxlint: reglas de hooks (exhaustive-deps) y corrección
npm run knip        # archivos, exports y dependencias sin uso
npm test            # vitest run — tests de componentes y de geometría
npm run check       # los cuatro anteriores, en orden (lo mismo que corre el CI)
npm run build       # typecheck + build de producción (genera el service worker)
npm run preview     # sirve dist/ — es la única forma de probar el service worker
npm run analyze     # build con mapas de fuente + dist/stats.html (qué pesa cada chunk)
```

**Por qué oxlint y no ESLint.** `typescript-eslint` no soporta TypeScript 7 (su
paquete ya no trae la API de JavaScript que usa el parser). oxlint no depende
del compilador y trae `react-hooks/exhaustive-deps`. Las variables sin uso las
ataja `tsc` (`noUnusedLocals`), así que el linter no las repite. La
configuración está en `.oxlintrc.json`; `react-perf` corre sólo sobre el mapa,
donde un objeto nuevo en cada render sí le cuesta trabajo a react-map-gl.

El service worker está desactivado en `npm run dev` a propósito: cachear durante
el desarrollo es una fuente inagotable de confusión. Para probarlo, usá
`npm run build && npm run preview`, o poné `devOptions.enabled: true` en
`vite.config.ts`.

---

## Estructura

```
frontend/
├── public/icons/            # iconos PWA (192, 512, maskable, apple-touch, favicon)
├── src/
│   ├── api/
│   │   ├── types.ts         # espejo TS de app/schemas/incident.py — fuente de verdad del contrato
│   │   ├── client.ts        # fetch + ApiError + armado de query strings
│   │   └── incidents.ts     # una función por endpoint
│   ├── domain/
│   │   ├── families.ts          # familia de cada IncidentType + textos por familia
│   │   ├── powerSymbology.ts    # cortes: acento por distribuidora
│   │   ├── palette.ts           # ← resuelve cuál de las 3 paletas aplica
│   │   ├── symbology.ts         # incendios: paleta cálida + helpers compartidos
│   │   ├── trafficSymbology.ts  # accidentes viales: paleta fría
│   │   ├── otherSymbology.ts    # inundación, derrumbe, rescate: paleta teal
│   │   ├── seismicSymbology.ts  # sismos: escala por magnitud, capa aparte
│   │   └── labels.ts            # enums del backend → castellano
│   ├── hooks/
│   │   ├── useActiveIncidents.ts   # polling de /incidents/active
│   │   ├── useIncidentDetail.ts    # /incidents/{code}, sólo con la ficha abierta
│   │   ├── useFreshness.ts         # edad real del dato en pantalla
│   │   └── useOnlineStatus.ts
│   ├── components/
│   │   ├── map/
│   │   │   ├── IncidentMap.tsx     # MapLibre + fuente GeoJSON (+ pin fantasma)
│   │   │   ├── incidentLayers.ts   # las 4 capas de círculos
│   │   │   └── MapLegend.tsx       # leyenda compacta en dos niveles (LegendBody)
│   │   ├── shell/
│   │   │   ├── ExplorePanel.tsx    # resumen + Historial · Capas · Leyenda, o la ficha
│   │   │   ├── DesktopColumn.tsx   # la columna izquierda de escritorio
│   │   │   └── BottomSheet.tsx     # la hoja inferior de tres alturas del teléfono
│   │   ├── feed/
│   │   │   └── HistoryFeed.tsx     # historial de 24 h: En el mapa · Hoy · Ayer
│   │   ├── incident/
│   │   │   ├── IncidentSheet.tsx   # ficha; incrustada en la columna/hoja (DetailSurface)
│   │   │   ├── ConfidenceBar.tsx   # una barra por eje, rotulada
│   │   │   ├── ConfidenceAudit.tsx # despliegue de confidence_breakdown
│   │   │   ├── SourceChips.tsx
│   │   │   └── AlertBadge.tsx
│   │   └── ui/                     # AppHeader, StalenessBanner, MapOverlayState
│   ├── lib/                        # queryClient, geojson, format (es-CL)
│   ├── config/                     # env tipado, encuadre y límites del mapa
│   ├── App.tsx
│   └── index.css                   # Tailwind v4 + tokens
├── vite.config.ts                  # proxy, chunking, manifest y workbox
└── tsconfig.json
```

---

## Las tres decisiones que explican el diseño

### 1. El color codifica el tramo de confianza (política v2.0.0)

El color del pin lo decide `confidence_level`, el tramo de tres estados que
calcula el motor. Los tres hex y los dos cortes son **los que declara el
backend** en `app/models/enums.py` (`LEVEL_STYLES`, `UNSAFE_THRESHOLD`,
`CONFIRMED_THRESHOLD`); el frontend sólo los replica.

| Tramo | Confianza | Color | Significa |
|---|---|---|---|
| `unsafe` | < 30 % | rojo `#dc2626` | Señal aislada sin corroborar. Puede ser ruido. |
| `possible` | 30 % – 60 % | amarillo `#eab308` | Hay evidencia, no alcanza para afirmar que hay fuego. |
| `confirmed` | > 60 % | naranja `#ea580c` | Evidencia acumulada por sobre el 60 %. |

Los bordes son exactos: 0.30 ya es `possible` y **0.60 exacto todavía lo es**;
sólo se cruza a `confirmed` por encima de 0.60.

**El rojo advierte sobre el dato, no sobre el fuego.** Es una inversión
deliberada respecto de la convención habitual de mapas de emergencia, y por eso
se compensa en dos lugares: la leyenda lo dice con todas las letras, y el radio
del pin crece con el tramo (`unsafe` 0.58× → `confirmed` 1.0×) para que una
señal sin corroborar no domine el mapa por encima de un incendio real.

El cliente recalcula el tramo con `levelFor()` **sólo** si la respuesta no trae
`confidence_level` — en la práctica, una respuesta antigua servida desde la
caché del service worker.

### 1b. Lo que el color NO puede decir

La escala ocupa el canal del color por completo, así que dos cosas que siguen
importando se codifican como textura:

- **Estado.** Un incidente cerrado (`controlled`, `extinguished`, `stale`,
  `merged`, `dismissed`) usa el mismo color desaturado —derivado con `mute()`,
  no escrito a mano— con menos opacidad y un anillo gris. Un incendio extinguido
  no puede verse igual que uno ardiendo.
- **Verificación institucional.** `confidence_level = 'confirmed'` significa
  "la evidencia supera el 60 %", **no** "CONAF fue al lugar". El backend lo
  advierte dos veces: un racimo de despachos radiales llega a `confirmed` con
  `is_official_confirmed = false`. Esos pines llevan un punto central hueco, y
  la ficha muestra el aviso explícito. Sin eso, el naranja rotulado "Incendio
  confirmado" le atribuiría a CONAF una confirmación que nunca hizo.

El filtro del encabezado dice "Verificados en terreno" porque el parámetro
`confirmed_only` del backend filtra por `is_official_confirmed`, no por el tramo.

### 1c. El anillo de SENAPRED y el aro blanco

El anillo exterior sigue siendo el eje de alertas de SENAPRED, independiente de
todo lo anterior. Entre el relleno y ese anillo hay un **aro blanco** que no es
decorativo: desde la v2.0.0 el relleno puede ser rojo (`#dc2626`) o amarillo
(`#eab308`), y el anillo de alerta también es rojo (`#e11d48`) o amarillo
(`#f59e0b`). Sin un aro neutro en medio, un incidente `unsafe` con alerta roja
sería una mancha roja uniforme y los dos ejes se volverían ilegibles justo
cuando más importan.

### 1c-bis. Tres familias, tres paletas, las mismas capas

Desde que existen los accidentes viales, el color lo decide primero la
**familia** del incidente y después el tramo de confianza:

| Capa | Familias | < 30 % | 30-60 % | > 60 % |
|---|---|---|---|---|
| Incendios | `fire` | `#dc2626` | `#eab308` | `#ea580c` |
| Accidentes viales | `traffic` | `#22d3ee` | `#4338ca` | `#6b21a8` |
| Otras emergencias | `hydro`, `other` | `#5eead4` | `#0d9488` | `#115e59` |

Cálido para fuego, frío para tráfico, teal para el resto. No es estético: en una
emergencia real conviven en pantalla, y un choque en la Ruta 68 no puede
competir visualmente con un incendio forestal. Ninguna paleta comparte un solo
hex con otra —ni con la de sismos—, y hay una comprobación que lo verifica.

Las tres familias comparten las **mismas capas de MapLibre**. Sólo cambia un
`match` sobre la propiedad `layer`; el radio, el aro blanco, el anillo de alerta
de SENAPRED y la marca de no verificado son idénticos. Duplicar las capas por
familia habría triplicado el costo de render para variar un color.

**`family` no viaja en la API.** `IncidentRead` expone `type` pero no la familia,
así que `domain/families.ts` replica la tabla `INCIDENT_FAMILY` del backend, con
una comprobación que compara ambas y falla si divergen. Lo correcto sería que el
backend la expusiera como `computed_field` —una línea en `IncidentRead`— y esta
tabla desaparecería; vale la pena cuando se toque el schema.

### 1c-ter. Los textos también dependen de la familia

El backend declara `LEVEL_STYLES[confirmed].label = "Incendio confirmado"`, que
es correcto para fuego y falso para un choque. Mientras no tenga etiquetas por
familia, las de tráfico y otras emergencias se definen en el frontend.

Lo mismo con dos cosas que estaban escritas a mano en la ficha:

- **Quién verifica en terreno.** Decir «ni CONAF ni Bomberos» sobre un accidente
  vial es incorrecto: CONAF no atiende choques. `VERIFYING_SOURCES` tiene la
  frase afirmativa y la negativa completas por familia, en vez de derivar una de
  la otra con un reemplazo de texto que produce castellano roto.
- **El número de emergencia.** 132 Bomberos para incendios, 133 Carabineros para
  accidentes (más 131 SAMU si hay lesionados). Mandar a llamar a Bomberos por un
  choque sin fuego retrasa la respuesta correcta.

### 1c-quater. Cortes de suministro: el color lo decide la empresa

La familia `power` rompe a propósito la regla de las otras tres. En ellas el
color codifica `confidence_level` porque hay una duda real sobre si el fenómeno
existe; en un corte no la hay — la señal viene de la propia distribuidora, que
sabe qué circuitos tiene caídos, y el collector le asigna confianza 1,0. Un
tramo que siempre vale lo mismo no informa nada, así que el canal del color se
usa para lo que sí varía y sí le importa a un vecino: **qué empresa** repone.

| Distribuidora | Acento |
|---|---|
| Chilquinta | rojo profundo `#b91c1c` |
| CGE | azul oscuro `#1e3a8a` |

Los cortes tienen **su propia fuente GeoJSON**, separada de la de incidentes
(`components/map/outageLayers.ts`): un disco del color de la empresa con el
rayo SDF `av-bolt` encima. Es la **única fuente agrupada**: en un temporal son
decenas de cortes en la misma comuna, y a escala regional lo que importa es
dónde y a cuántos clientes, que es lo que dice un racimo (suma
`affected_clients`). Tocar un racimo acerca la cámara; desde zoom 12 cada corte
va suelto. Los incendios, en cambio, no se agrupan nunca: un mapa de
emergencias no puede esconder un fuego detrás de un número.

Antes eran `<Marker>` del DOM con un tope de 150: pasado el tope, el resto
desaparecía del mapa sin aviso mientras el panel los seguía contando.

### 1c-quater-bis. Cortes de agua (Esval): información de servicio

Una fila más del panel de emergencias, junto a la luz, pero **no es un
incidente**: `water_cut` está fuera del motor de correlación en el backend, no
crea incidentes ni manda push, y sale por su propia ruta,
`/events/water-cuts/geojson`.

- **Vigente = visto en la última lectura de la API de Esval.** Esval no avisa
  cuándo termina un corte, y su hora de término es referencial. Si el collector
  cae, se ve lo último conocido y la fila avisa que el cero no es calma.
- **La fila aparece sola cuando llegan datos.** Esval sólo responde a IP
  chilenas y el backend sale por un proxy en Chile (`infra/proxy-cl/`); hasta
  que el backend lo lee por primera vez (`fuente.ultima_lectura`), la fila no
  existe. `VITE_WATER_CUTS=off` la apaga de emergencia.
- **En el mapa**, disco cian oscuro `#0e7490` con la gota SDF `av-drop`, en su
  propia fuente y **por debajo** de los cortes de luz y los incidentes: un
  incendio en el mismo punto gana el dibujo y el toque. Sin agrupar (un mal día
  son ~30). Apagar la fila es `visibility`, sin `setData`.
- **Al tocarlo**, una tarjeta con la forma de la del sismo (`WaterCutCard`):
  inicio, reposición estimada (en ámbar si ya pasó), motivo y el enlace al
  visor de Esval. **No hay fila de clientes afectados**: Esval no publica ese
  dato, y un cero afirmaría lo contrario de «no se sabe».
- Un corte sin punto (el visor de Esval no respondió) cuenta en la lista y abre
  la tarjeta, pero no se dibuja ni mueve la cámara.

### 1c-quinquies. Amenaza sísmica: capa de referencia, no de emergencia

`/api/v1/events/seismic/hazard` (que sirve `backend/static/geo/amenaza_sismica_valpo.json`)
es el modelo probabilístico del CSN (MASCSN26): una grilla de celdas con
`pga_475`, `pga_2475` y aceleraciones espectrales. Describe cuánto **puede** moverse el suelo, no lo que está pasando,
así que todo su diseño busca que no se lea como un evento:

- **Violeta**, fuera de la familia cálida que usan incendios y sismos reales.
- **Rampa continua** sobre `pga_475` en vez de bandas: un degradado se lee como
  campo de fondo, unos escalones duros como zonas declaradas por alguien.
- **Dos rampas, una por tema.** Sobre fondo oscuro más amenaza es más brillante;
  sobre fondo claro, más oscuro. Una sola rampa dejaría el extremo alto
  invisible en uno de los dos.
- Va en su propia sección del panel, bajo el título «Capas de referencia», y con
  un interruptor en vez de una casilla.

**Carga diferida sin re-descarga.** `useSeismicHazard` lleva dos banderas:
`enabled` (lo que el usuario pide ahora) y `hasMounted` (si la fuente llegó a
existir). `hasMounted` es monótona, y de ahí salen las dos propiedades: mientras
nadie encienda la capa el `<Source>` no se monta y el archivo no se pide; una vez
montado no se desmonta nunca, y apagar la capa sólo cambia `layout.visibility`.
Atar el montaje directamente a `enabled` se ve idéntico y vuelve a descargar el
archivo en cada encendido.

**Jerarquía por `beforeId`, no por z-index.** MapLibre no tiene z-index: dibuja
en el orden del arreglo de capas del estilo. Las dos capas de amenaza se anclan
con `beforeId` a `seismic-reach-fill`, la primera capa de emergencia. Si esa
constante deja de coincidir con una capa real, MapLibre ignora el anclaje **en
silencio** y la amenaza pasa a taparlo todo; hay un test que ata ambos
identificadores.

### 1d. La capa de sismos usa una escala completamente aparte

`domain/seismicSymbology.ts` no comparte nada con `domain/symbology.ts`, y es a
propósito. Los incendios se colorean por `confidence_level`, que mide *cuánta
evidencia hay de que el hecho exista*. Un sismo registrado por una red
sismológica no tiene esa duda: es un hecho medido. Acá el color codifica
**magnitud**, que es intensidad física.

| Banda | Magnitud | Color |
|---|---|---|
| Menor | M < 4,0 | amarillo `#facc15` |
| Moderado | M 4,0 – 5,5 | naranja `#f97316` |
| Fuerte | M > 5,5 | rojo oscuro `#991b1b` |
| Sin magnitud | preliminar | gris `#64748b` |

Que ambas escalas usen tonos cálidos es una coincidencia desafortunada, no un
vínculo. Se compensa con la **forma**: los sismos son círculos huecos de trazo
grueso, los incidentes discos sólidos. Se distinguen sin leer la leyenda, y no
comparten ni un hex (hay un test que lo verifica).

Dos detalles que no son obvios:

- **El radio crece de forma perceptual, no logarítmica.** Un M6 libera unas mil
  veces más energía que un M4; reproducir eso daría un punto invisible y una
  mancha que taparía media región. La magnitud se acota a [2, 7] y se interpola
  linealmente. Es una decisión de legibilidad, y por eso la leyenda muestra los
  tamaños en vez de dejarlos a interpretación.
- **`magnitude` puede ser `null`.** El USGS publica la detección antes de
  calcular la magnitud. Esos sismos tienen banda propia y tamaño mínimo: pintarlos
  en la banda baja afirmaría que fueron menores, que es justo lo que no se sabe.
  Las soluciones `automatic` se dibujan con trazo más tenue.

La consulta se apaga con la capa (`enabled` en `useSeismicEvents`): no tiene
sentido gastar red trayendo sismos que nadie está mirando. Su cadencia es más
lenta que la de incidentes (3 min contra 1) porque el collector del USGS corre
cada 5 y un sismo no cambia de estado una vez ocurrido.

### 1e. Cuánto se queda algo en el mapa, y el historial

El motor pasa un incidente a `stale` tras 12 h sin señales: ese reloj mide si
una señal nueva es el mismo hecho, y no se toca. Lo que se ve en el mapa tiene
su propia ventana, por familia y desde la última señal
(`domain/displayWindow.ts`):

| Familia | En el mapa |
|---|---|
| Incendios | 6 h |
| Accidentes viales | 2 h |
| Otras emergencias | 4 h |
| Cortes de luz | mientras la empresa lo liste (`outage.vigente`); 4 h si el backend no sabe |

Lo que sale del mapa baja al **historial de 24 h** (pestaña «Historial»), con
un estado que nunca inventa un fin: «Sin novedades» (dejaron de llegar
señales), «Ya no figura» (la empresa dejó de listar el corte), «Controlado» o
«Extinguido». Los cortes de luz de una comuna se agrupan en una fila. Abrir
algo que ya salió del mapa lo marca con un pin fantasma gris.

Una sola consulta alimenta mapa e historial (`hours=48` con `active`,
`controlled`, `stale` y `extinguished`). `useDisplaySplit` agenda un único
temporizador para el instante en que algo vence: no hay reloj en `App`.

### 1f. Una columna en escritorio, una hoja en el teléfono

Como en Watch Duty o VicEmergency: el contenido en una columna y el mapa libre
al lado. Es el mismo componente (`components/shell/ExplorePanel.tsx`) en dos
contenedores:

- **Escritorio** (≥ 768 px): columna izquierda de 22 rem. La escala del mapa y
  el botón «Reportar emergencia» se corren a la derecha de ella, y la cámara
  centra lo enfocado en el mapa visible (`lib/cameraOffset.ts`).
- **Teléfono**: hoja inferior asomada, a media pantalla o completa
  (`lib/sheetStore.ts`); se arrastra desde el encabezado. «Reportar» va en el
  encabezado de la hoja, siempre a mano.

La ficha de un incidente, un sismo o un corte de agua reemplaza al contenido de
la columna en el mismo lugar (`DetailSurface`); cerrarla lo devuelve.

### 2. Offline sí, pero con la edad del dato a la vista

El service worker cachea el app shell y los tiles, y sirve `/incidents/*` con
`NetworkFirst` (timeout 6 s, expiración 10 min). Eso permite abrir la app en una
quebrada sin señal.

**Las reglas de la API son funciones, no expresiones regulares.** En producción
la API vive en otro origen (Render) que la PWA (Vercel), y Workbox sólo aplica
una RegExp a una URL de otro origen si calza desde el primer carácter. Con
`/\/api\/v1\/incidents\//` las reglas no guardaban nada en producción; un
matcher `({ url }) => url.pathname.startsWith(…)` sirve en los dos casos.

La contraparte obligatoria es `StalenessBanner`: si la respuesta tiene más de
`VITE_STALE_AFTER_MS` (3 min por defecto) o no hay conexión, aparece una franja
ámbar con la antigüedad exacta del dato. Sin ese cartel, cachear una app de
emergencias sería una forma de desinformar.

### 3. Una sola fuente de datos

El backend ofrece `/incidents/active` (JSON tipado) y `/incidents/geojson`. La
PWA usa **sólo `/active`** y arma el GeoJSON en el cliente
(`src/lib/geojson.ts`). Motivo: la ficha necesita el objeto completo de todos
modos, y mantener dos vistas del mismo mundo es una manera segura de que se
desincronicen.

Al GeoJSON sólo viajan propiedades escalares — MapLibre serializa los arreglos
anidados, así que `sources` se lee del objeto tipado, no del feature.

---

## Variables de entorno

| Variable | Por defecto | Para qué |
|---|---|---|
| `VITE_API_BASE_URL` | `/api/v1` | Base de la API |
| `VITE_DEV_API_PROXY` | `http://localhost:8000` | Destino del proxy de desarrollo (sólo `vite.config.ts`) |
| `VITE_POLL_INTERVAL_MS` | `60000` | Cadencia del polling de incidentes |
| `VITE_SEISMIC_POLL_INTERVAL_MS` | `180000` | Cadencia de la capa de sismos |
| `VITE_RAIN_POLL_INTERVAL_MS` | `600000` | Cadencia de la lluvia (sólo encendida) |
| `VITE_WEATHER_POLL_INTERVAL_MS` | `600000` | Cadencia del widget meteorológico |
| `VITE_ROAD_CLOSURE_POLL_INTERVAL_MS` | `900000` | Cadencia de los cortes de ruta (sólo encendida) |
| `VITE_VEHICLE_RADAR` | `on` en dev, `off` en build | Radar de vehículos (GBV) |
| `VITE_VEHICLE_POLL_INTERVAL_MS` | `300000` | Cadencia del radar |
| `VITE_WATER_CUTS` | `on` | Cortes de agua (Esval). La fila aparece cuando el backend ya tiene datos |
| `VITE_WATER_CUT_POLL_INTERVAL_MS` | `300000` | Cadencia de los cortes de agua |
| `VITE_STALE_AFTER_MS` | `180000` | Umbral del aviso de antigüedad |
| `VITE_MAP_STYLE` / `VITE_MAP_STYLE_DARK` | CARTO Positron / Dark Matter | Estilo del mapa base |

**Todas son públicas**: Vite las copia al bundle. `vite.config.ts` rechaza el
build si el nombre de una `VITE_` parece un secreto (`TOKEN`, `SECRET`, `KEY`…).

El polling está en 60 s porque el worker de correlación corre cada 120 s
(`CORRELATION_POLL_INTERVAL_SECONDS`). Pedir más seguido gasta batería sin traer
datos nuevos. Si cambia esa constante en el backend, cambiala acá también.

TanStack Query pausa el polling cuando la pestaña pierde el foco y refresca al
volver, así que la app no consume batería en el bolsillo. Con el backend caído,
`lib/polling.ts` frena el sondeo (duplica la espera en cada fallo, hasta 5 min,
con ±15 % de jitter) y el primer éxito lo devuelve a la cadencia base. Un 429 o
503 con `Retry-After` se reintenta cuando el servidor lo pidió.

---

## Pendientes antes de producción

- **Mapa base.** CARTO Positron no pide API key y sirve para desarrollo, pero su
  uso gratuito tiene límites. Para producción conviene MapTiler, Protomaps
  autoalojado o tiles propios. Se cambia sólo con `VITE_MAP_STYLE`.
- **CORS.** Si la PWA no se sirve desde el mismo origen que la API, agregá el
  dominio a `CORS_ORIGINS` del backend y apuntá `VITE_API_BASE_URL` a la URL
  absoluta.
- **`POST /incidents/correlate`** debe quedar detrás de autenticación de
  operador; la PWA no lo llama.
- **Logos de las distribuidoras.** Los pines usan un acento de color y el glifo
  `av-bolt`, no los logos de Chilquinta y CGE: son marcas registradas y usarlas
  exige permiso. El acento está aislado en `powerSymbology.ts`.
- **Exponer `family` en la API.** Hoy `domain/families.ts` replica
  `INCIDENT_FAMILY` del backend. Un `computed_field` en `IncidentRead` elimina la
  tabla duplicada y su riesgo de deriva.
- **Separar «Otras emergencias».** Agrupa `hydro` (inundación, derrumbe) y
  `other` (rescate) porque ninguna tiene fuente propia todavía. Cuando SENAPRED
  aporte alertas por crecida, `hydro` merece su capa y su paleta.
- **Etiquetas por familia en el backend.** `LEVEL_STYLES` es hoy implícitamente
  de incendios; las de tráfico viven en el frontend por eso.
- Historial y filtros por comuna quedan para la siguiente iteración (ver
  `context.md`). Los avisos push ya están: ver
  `backend/docs/notificaciones-push.md`. Se prueban con `npm run build && npm
  run preview`, porque el service worker está apagado en `npm run dev`.
- **Tests visuales.** `npm test` cubre comportamiento y geometría bajo jsdom, que
  no calcula layout ni renderiza píxeles. Una regresión puramente visual —un
  color mal contrastado, un solape— no la atrapa. Las pruebas de humo con
  Chromium (29-sep-2026) se corrieron a mano; falta dejarlas en el repo con
  capturas de referencia. Con `?debug=1`, la instancia del mapa queda en
  `window.__ALERTAV_MAP__` para inspeccionarla.
- **Accesibilidad.** Los objetivos táctiles y `prefers-reduced-motion` están
  cubiertos; falta una pasada de contraste sobre el amarillo `single_signal`
  cuando se usa como fondo de chip.

---

## Verificaciones hechas

- `tsc --noEmit` limpio (TypeScript 7, `strict` + `noUncheckedIndexedAccess`).
- `vite build` limpio; service worker con las entradas precacheadas y las reglas
  de runtime caching (funciones para la API) presentes en `dist/sw.js`.
- `App.renders.test.tsx`: con la app quieta un minuto, y al seleccionar un
  incidente, un sismo o el radar, el mapa, el panel y la barra no se repintan.
- Contraste automático contra `backend/app/schemas/incident.py` y
  `backend/app/models/enums.py`: campos de `IncidentRead` (incluido
  `confidence_level`), `IncidentEventLink`, `IncidentStats` y los enums. La
  paleta y los cortes se leen del `LEVEL_STYLES` del backend y se comparan hex a
  hex con `domain/symbology.ts`.
- Umbrales ejercitados en los bordes exactos: 0.2999 → `unsafe`, 0.30 →
  `possible`, 0.60 → `possible`, 0.6001 → `confirmed`.
- Casos críticos cubiertos: `confirmed` sin verificación institucional lleva
  marca; incidente extinguido se atenúa sin cambiar de tramo; respuesta sin
  `confidence_level` recalcula desde `confidence`.
- Dev server arrancado contra un backend simulado: proxy, orden `lon,lat` y
  serialización del listado verificados de punta a punta.
