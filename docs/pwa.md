# PWA

Decisiones de diseño de la interfaz: qué dice cada color, qué no puede decir y
cómo se mantiene honesta la edad del dato. Cómo correrla está en
[`frontend/README.md`](../frontend/README.md).

## El color codifica el tramo de confianza (política v2.0.0)

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

## Lo que el color NO puede decir

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

## El anillo de SENAPRED y el aro blanco

El anillo exterior sigue siendo el eje de alertas de SENAPRED, independiente de
todo lo anterior. Entre el relleno y ese anillo hay un **aro blanco** que no es
decorativo: desde la v2.0.0 el relleno puede ser rojo (`#dc2626`) o amarillo
(`#eab308`), y el anillo de alerta también es rojo (`#e11d48`) o amarillo
(`#f59e0b`). Sin un aro neutro en medio, un incidente `unsafe` con alerta roja
sería una mancha roja uniforme y los dos ejes se volverían ilegibles justo
cuando más importan.

## Tres familias, tres paletas, las mismas capas

El color lo decide primero la **familia** del incidente y después el tramo de
confianza:

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

**La tabla de familias existe dos veces.** `domain/families.ts` replica
`INCIDENT_FAMILY` del backend para clasificar sin esperar la respuesta, y una
comprobación compara ambas y falla si divergen.

## Los textos también dependen de la familia

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

## Cortes de suministro: el color lo decide la empresa

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

## Cortes de agua (Esval): información de servicio

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

## Amenaza sísmica: capa de referencia, no de emergencia

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
- En la pestaña «Capas» va en su propio grupo, «Referencia», separado del
  mosaico de emergencias (ver la sección siguiente a la columna).

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

## La capa de sismos usa una escala completamente aparte

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

## Cuánto se queda algo en el mapa, y el historial

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
un estado que nunca inventa un fin: «Sin novedades desde HH:MM» (dejaron de
llegar señales), «Probablemente repuesto» (la empresa dejó de listar el corte),
«Controlado» o «Extinguido». Los cortes de luz de una comuna se agrupan en una fila. Abrir
algo que ya salió del mapa lo marca con un pin fantasma gris.

Una sola consulta alimenta mapa e historial (`hours=48` con `active`,
`controlled`, `stale` y `extinguished`). `useDisplaySplit` agenda un único
temporizador para el instante en que algo vence: no hay reloj en `App`.

## Una columna en escritorio, una hoja en el teléfono

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

## La pestaña «Capas»: un mosaico, no casillas

`components/layers/LayersTab.tsx`. Tres grupos con el mismo rótulo que las
secciones de «Ahora»:

- **Emergencias.** Un azulejo por capa (`LayerTile`, un `<button aria-pressed>`)
  con el glifo y el color del mapa (`domain/layers.ts`). Encendido se tiñe del
  color de la capa; apagado queda en contorno gris. Arriba a la derecha, lo que
  la capa tiene en el mapa; bajo el nombre, «Sin datos» cuando un cero no es
  calma (`LayerHealth`). Debajo, los filtros finos como fichas (`Chip`):
  empresa de luz, relevancia de los sismos y «sólo verificados»; los de una capa
  sólo existen con la capa encendida.
- **Referencia.** Amenaza sísmica, cortes de ruta y cuarteles, apagadas al
  abrir. Al encender una, su ficha aparece bajo el mosaico con la leyenda, el
  estado y el reintento (`ReferenceLayers`).
- **Leyenda**, plegada: es consulta, no control.

Una casilla dice «seleccionar» y la dibuja el sistema, distinta en cada
plataforma; una capa es «ver u ocultar», y el azulejo lo muestra con lo mismo
que se ve en el mapa. No hay lista de incidentes por capa: es la de «Ahora».

**Nada se recuerda entre visitas.** Quien abre la app porque huele a humo no
puede encontrarse los incendios apagados desde la semana pasada. Mientras algo
esté oculto (capas, empresas o verificados), la pestaña lleva un punto y el grupo
ofrece «Mostrar todo».

## Offline sí, pero con la edad del dato a la vista

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

## Una sola fuente de datos

El backend ofrece `/incidents/active` (JSON tipado) y `/incidents/geojson`. La
PWA usa **sólo `/active`** y arma el GeoJSON en el cliente
(`src/lib/geojson.ts`). Motivo: la ficha necesita el objeto completo de todos
modos, y mantener dos vistas del mismo mundo es una manera segura de que se
desincronicen.

Al GeoJSON sólo viajan propiedades escalares — MapLibre serializa los arreglos
anidados, así que `sources` se lee del objeto tipado, no del feature.
