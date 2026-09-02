/**
 * Widget meteorológico táctico de la barra superior.
 *
 * # El test que más vale de este archivo
 *
 * `no confunde "no hay dato" con "no pasa nada"`. Es el modo de fallo caro de
 * cualquier barra de estado: si el collector lleva seis horas caído durante un
 * temporal, un widget que muestre la calma estará afirmando algo que no sabe, en
 * el único elemento de la interfaz que el usuario mira sin mirar.
 *
 * Los demás cubren las tres reglas del rediseño:
 *
 *   1. **El estado silencioso no compite con el mapa.** Sin fondo, sin borde y
 *      sin latido: sólo tipografía sobre el cromo.
 *   2. **El estado de alerta expande la métrica culpable**, y la expande con la
 *      cifra que mandó el backend — no con una recalculada acá.
 *   3. **Nada de esto se disfraza de alerta oficial.** La cautela que la capa
 *      arrastra en todos sus textos también vale para 180 px de barra.
 *
 * Hereda además la cobertura de lluvia que vivía en `ReferenceDock.test.tsx`:
 * la tarjeta se fusionó acá, y con ella su interruptor.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WeatherWidget } from './WeatherWidget'
import { __resetWeatherStore } from '@/lib/tacticalWeatherStore'

/* --- Fábricas --------------------------------------------------------------
 *
 * La respuesta se arma con la forma exacta que emite el backend y se sirve por
 * `fetch`, no por un doble del módulo de API. Así el test cubre también
 * `parseTacticalWeather`, que es donde vive el riesgo de que el contrato se
 * mueva.
 */

const CALMA = {
  observado_en: '2026-09-02T14:00:00+00:00',
  inicio: '2026-09-02T14:00:00+00:00',
  fin: '2026-09-03T14:00:00+00:00',
  severidad: 'ninguna',
  amenaza: null,
  disparo_principal: null,
  comuna_origen: null,
  temp_c: 16.4,
  viento_kmh: 12.0,
  temp_max_c: 21.0,
  temp_min_c: 11.0,
  humedad_min: 62.0,
  rafaga_max_kmh: 24.0,
  uv_max: 4.0,
  comunas: 36,
  con_lluvia: 0,
  en_aviso: 0,
  en_critico: 0,
  comunas_en_alerta: [],
  modelo: 'best_match',
  es_pronostico: true,
}

/** Una tarde de febrero en Petorca: calor crítico. */
const CALOR = {
  ...CALMA,
  severidad: 'critica',
  amenaza: 'calor',
  comuna_origen: 'Petorca',
  disparo_principal: {
    amenaza: 'calor',
    severidad: 'critica',
    metrica: 'temp_max_c',
    valor: 38.0,
    unidad: '°C',
    umbral: 36.0,
    texto: 'máxima de 38 °C ≥ 36 °C',
    momento: '2026-09-02T19:00:00+00:00',
  },
  temp_c: 27.5,
  temp_max_c: 38.0,
  humedad_min: 18.0,
  uv_max: 12.0,
  en_critico: 1,
  comunas_en_alerta: ['Petorca'],
}

/** Un temporal de invierno: remoción en masa por acumulado. */
const REMOCION = {
  ...CALMA,
  severidad: 'aviso',
  amenaza: 'remocion',
  comuna_origen: 'Valparaíso',
  disparo_principal: {
    amenaza: 'remocion',
    severidad: 'aviso',
    metrica: 'mm_3h_max',
    valor: 18.4,
    unidad: 'mm/3 h',
    umbral: 15.0,
    texto: 'acumulado en 3 h 18.4 mm ≥ 15.0 mm',
    momento: null,
  },
  con_lluvia: 12,
  en_aviso: 4,
  comunas_en_alerta: ['Valparaíso', 'Viña del Mar', 'Quilpué', 'Villa Alemana'],
}

/** «No hay ninguna corrida reciente». NO es lo mismo que la calma. */
const SIN_DATOS = {
  ...CALMA,
  observado_en: null,
  inicio: null,
  fin: null,
  temp_c: null,
  viento_kmh: null,
  temp_max_c: null,
  temp_min_c: null,
  humedad_min: null,
  rafaga_max_kmh: null,
  uv_max: null,
  comunas: 0,
}

function servir(payload: unknown, { ok = true }: { ok?: boolean } = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      ok
        ? new Response(JSON.stringify(payload), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          })
        : new Response('boom', { status: 500 }),
    ),
  )
}

/**
 * Monta y espera a que la primera lectura llegue.
 *
 * El store consulta al montarse el primer suscriptor, así que sin el `waitFor`
 * cada aserción correría contra el estado inicial de carga.
 */
async function montar(payload: unknown, opciones?: { ok?: boolean }) {
  servir(payload, opciones)
  render(<WeatherWidget />)
  await waitFor(() => expect(fetch).toHaveBeenCalled())
  // Deja resolver la promesa y aplicar el `patch` del store.
  await act(async () => {
    await Promise.resolve()
  })
}

const widget = () => screen.getByRole('button', { name: /estado meteorológico/i })

beforeEach(() => {
  __resetWeatherStore()
})

afterEach(() => {
  __resetWeatherStore()
  vi.unstubAllGlobals()
})

/* -------------------------------------------------------------------------- */

describe('los tres estados no se confunden entre sí', () => {
  it('no confunde "no hay dato" con "no pasa nada"', async () => {
    await montar(SIN_DATOS)

    // Un guion, no una temperatura, y el nombre accesible lo dice con palabras
    // para quien no ve el guion.
    expect(widget()).toHaveAccessibleName(/sin dato meteorológico/i)
    expect(widget()).not.toHaveAccessibleName(/sin umbrales cruzados/i)
  })

  it('una respuesta caída tampoco se lee como calma', async () => {
    await montar(null, { ok: false })

    expect(widget()).toHaveAccessibleName(/sin dato meteorológico/i)
  })

  it('en calma muestra la MEDIANA regional, no el máximo', async () => {
    await montar(CALMA)

    // 16 (mediana) y no 21 (máximo). Confundirlas haría que un día con 38 °C en
    // el interior y 17 en la costa anunciara 38 como si fuera el tiempo que
    // hace donde vive la gente.
    expect(screen.getByText('16°')).toBeInTheDocument()
    expect(screen.queryByText('21°')).not.toBeInTheDocument()
    expect(screen.getByText(/12 km\/h/)).toBeInTheDocument()
  })

  it('en calma no lleva fondo ni borde: no compite con el mapa', async () => {
    await montar(CALMA)

    const boton = widget()
    expect(boton.style.backgroundColor).toBe('')
    expect(boton.style.boxShadow).toBe('')
    // Y no late: el latido está reservado para la condición crítica.
    expect(boton.className).not.toMatch(/animate-pulse-soft/)
  })
})

describe('el estado de alerta expande la métrica responsable', () => {
  it('muestra la cifra culpable, no la temperatura ambiente', async () => {
    await montar(CALOR)

    // 38, el máximo que cruzó el umbral — no 27,5, que es la mediana del
    // estado silencioso. En alerta, el ambiente deja de ser la noticia.
    expect(screen.getByText('38')).toBeInTheDocument()
    expect(screen.queryByText('28°')).not.toBeInTheDocument()
    expect(screen.getByText('°C')).toBeInTheDocument()
  })

  it('pinta el crítico con fondo y lo hace latir', async () => {
    await montar(CALOR)

    const boton = widget()
    expect(boton.style.backgroundColor).not.toBe('')
    expect(boton.style.boxShadow).not.toBe('')
    expect(boton.className).toMatch(/animate-pulse-soft/)
    // `motion-reduce` lo apaga sin perder el color, que es lo que carga la señal.
    expect(boton.className).toMatch(/motion-reduce:animate-none/)
  })

  it('el aviso NO late: un ámbar que parpadea toda una tarde se ignora', async () => {
    await montar(REMOCION)

    const boton = widget()
    // Tiene color —es una alerta— pero no movimiento.
    expect(boton.style.backgroundColor).not.toBe('')
    expect(boton.className).not.toMatch(/animate-pulse-soft/)
  })

  it('distingue remoción en masa de anegamiento', async () => {
    await montar(REMOCION)

    // No son sinónimos: uno es el drenaje urbano saturándose en una hora y el
    // otro es el suelo perdiendo infiltración. Dos mecanismos, dos respuestas.
    expect(widget()).toHaveAccessibleName(/remoción en masa/i)
    expect(widget()).not.toHaveAccessibleName(/anegamiento/i)
  })

  it('respeta los decimales de cada métrica', async () => {
    await montar(REMOCION)

    // La lluvia va con un decimal —0,4 y 0,9 mm son cosas distintas— y la
    // temperatura sin ninguno.
    expect(screen.getByText('18.4')).toBeInTheDocument()
  })
})

describe('el detalle desplegable', () => {
  it('arranca cerrado: la barra no puede abrirse sola sobre el mapa', async () => {
    await montar(CALOR)

    expect(widget()).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('region', { name: /estado meteorológico/i })).toBeNull()
  })

  it('explica la alerta con el texto del backend y nombra la comuna', async () => {
    const user = userEvent.setup()
    await montar(CALOR)
    await user.click(widget())

    // El texto lo redacta el backend, que es quien conoce el umbral vigente.
    // Reescribirlo en el navegador sería tener la política en dos sitios.
    expect(screen.getByText(/máxima de 38 °C ≥ 36 °C.*Petorca/)).toBeInTheDocument()
    expect(screen.getByText(/calor extremo/i)).toBeInTheDocument()
  })

  it('nunca dice «ola de calor»: es un término con definición oficial', async () => {
    const user = userEvent.setup()
    await montar(CALOR)
    await user.click(widget())

    // La DMC la define por percentil 90 diario durante tres días consecutivos.
    // Este pronóstico no puede calcular ni una cosa ni la otra.
    expect(screen.queryByText(/ola de calor/i)).toBeNull()
  })

  it('nunca llama «inundación» a un pronóstico, y lo dice', async () => {
    const user = userEvent.setup()
    await montar(REMOCION)
    await user.click(widget())

    expect(screen.getByText(/SENAPRED/)).toBeInTheDocument()
    expect(screen.getByText(/no es una alerta oficial/i)).toBeInTheDocument()
    expect(screen.queryByText(/^\s*inundaci[óo]n\s*$/i)).toBeNull()
  })

  it('en calma dice cuántas comunas se evaluaron, no «sin datos»', async () => {
    const user = userEvent.setup()
    await montar(CALMA)
    await user.click(widget())

    expect(screen.getByText(/36 comunas evaluadas/i)).toBeInTheDocument()
    expect(screen.queryByText(/sin datos/i)).toBeNull()
  })

  it('sin corrida reciente explica que no sabe, en vez de callarse', async () => {
    const user = userEvent.setup()
    await montar(SIN_DATOS)
    await user.click(widget())

    expect(screen.getByText(/no significa que esté todo tranquilo/i)).toBeInTheDocument()
  })

  it('recorta la lista de comunas en alerta en vez de volcar las 36', async () => {
    const user = userEvent.setup()
    await montar(REMOCION)
    await user.click(widget())

    expect(screen.getByText(/Valparaíso, Viña del Mar/)).toBeInTheDocument()
  })

  it('se cierra con Escape', async () => {
    const user = userEvent.setup()
    await montar(CALMA)
    await user.click(widget())
    expect(widget()).toHaveAttribute('aria-expanded', 'true')

    await user.keyboard('{Escape}')
    expect(widget()).toHaveAttribute('aria-expanded', 'false')
  })
})

describe('el interruptor de la capa de lluvia, heredado del riel', () => {
  it('vive dentro del detalle y no en la cara del widget', async () => {
    const user = userEvent.setup()
    await montar(CALMA)

    // Cerrado no hay interruptor: uno en la barra superior se toca por error
    // con el pulgar, y lo que hace es encender una capa del mapa.
    expect(screen.queryByRole('switch')).toBeNull()

    await user.click(widget())
    expect(screen.getByRole('switch', { name: /lluvia en el mapa/i })).toBeInTheDocument()
  })

  it('arranca apagado: nada se pide sin un gesto del usuario', async () => {
    const user = userEvent.setup()
    await montar(CALMA)
    await user.click(widget())

    expect(screen.getByRole('switch', { name: /lluvia en el mapa/i })).toHaveAttribute(
      'aria-checked',
      'false',
    )
  })

  it('se enciende y el estado sobrevive a cerrar el detalle', async () => {
    const user = userEvent.setup()
    await montar(CALMA)
    await user.click(widget())
    await user.click(screen.getByRole('switch', { name: /lluvia en el mapa/i }))

    expect(screen.getByRole('switch', { name: /lluvia en el mapa/i })).toHaveAttribute(
      'aria-checked',
      'true',
    )

    // Cerrar y volver a abrir: la intención vive en el store, no en el árbol
    // que se desmonta. Es lo que evita que girar el teléfono apague la capa.
    await user.keyboard('{Escape}')
    await user.click(widget())
    expect(screen.getByRole('switch', { name: /lluvia en el mapa/i })).toHaveAttribute(
      'aria-checked',
      'true',
    )
  })
})

describe('resistencia del contrato', () => {
  it('una severidad crítica sin disparo legible se degrada a calma', async () => {
    // Estado imposible por construcción del backend, y si llegara el widget se
    // pintaría de rojo sin poder decir por qué. Una barra roja que no explica
    // nada es peor que una gris.
    const roto = { ...CALOR, disparo_principal: { amenaza: 'calor' } }
    await montar(roto)

    expect(widget().className).not.toMatch(/animate-pulse-soft/)
    expect(widget()).toHaveAccessibleName(/sin umbrales cruzados/i)
  })

  it('una amenaza desconocida no rompe el render', async () => {
    const futuro = {
      ...CALOR,
      amenaza: 'granizo',
      disparo_principal: { ...CALOR.disparo_principal, amenaza: 'granizo' },
    }
    await montar(futuro)

    // Cae a calma —no hay glifo ni rótulo para algo que no existe— pero la
    // barra sigue en pie, que es lo único innegociable acá.
    expect(widget()).toBeInTheDocument()
  })

  it('un payload que no es un objeto no tumba la barra', async () => {
    await montar('vaya')

    expect(widget()).toHaveAccessibleName(/sin dato meteorológico/i)
  })
})

describe('accesibilidad', () => {
  it('el nombre accesible empieza por lo que el widget muestra (WCAG 2.5.3)', async () => {
    await montar(CALOR)

    // Quien navega por voz dice «pulsa Estado meteorológico», y el reconocedor
    // compara contra el nombre accesible. Un `aria-label` que describa mejor
    // pero no empiece por la etiqueta visible deja el control inalcanzable.
    expect(widget().getAttribute('aria-label')).toMatch(/^Estado meteorológico\./)
  })

  it('el glifo es decorativo: la información va en el texto', async () => {
    const { container } = render(<div />)
    void container
    await montar(CALOR)

    for (const svg of Array.from(document.querySelectorAll('svg'))) {
      expect(svg).toHaveAttribute('aria-hidden')
    }
  })
})

/* ---------------------------------------------------------------------------
 * Capa encendida y cero milímetros
 * ------------------------------------------------------------------------ */

describe('la capa encendida sin lluvia pronosticada lo dice', () => {
  /**
   * El problema: encender el interruptor con 0 mm en toda la región no produce
   * ningún cambio visible, y un mapa vacío admite dos lecturas opuestas —«no va
   * a llover» y «la capa no cargó»—. El aviso resuelve esa ambigüedad, y va
   * junto al interruptor porque es ahí donde está el gesto que la creó.
   */
  it('avisa cuando el pronóstico regional es cero', async () => {
    const user = userEvent.setup()
    await montar(CALMA) // con_lluvia: 0
    await user.click(widget())

    expect(screen.queryByText(/sin precipitaciones pronosticadas/i)).toBeNull()

    await user.click(screen.getByRole('switch', { name: /lluvia en el mapa/i }))
    expect(screen.getByText(/sin precipitaciones pronosticadas para hoy/i)).toBeInTheDocument()
  })

  it('no lo dice cuando sí hay comunas con lluvia', async () => {
    const user = userEvent.setup()
    await montar(REMOCION) // con_lluvia: 12
    await user.click(widget())
    await user.click(screen.getByRole('switch', { name: /lluvia en el mapa/i }))

    expect(screen.queryByText(/sin precipitaciones pronosticadas/i)).toBeNull()
  })

  /**
   * El test que más vale de este bloque, y es el mismo criterio que gobierna el
   * widget entero: **los dos ceros no son el mismo cero.**
   *
   * `con_lluvia: 0` con una corrida reciente significa que se consultaron las
   * 36 comunas y ninguna tiene lluvia. `observado_en: null` significa que no
   * hay pronóstico ninguno. Afirmar «no va a llover» en el segundo caso sería
   * inventar un dato que nadie emitió, en la interfaz de una aplicación que la
   * gente mira para decidir si sale de casa.
   */
  it('con el collector caído NO afirma que no va a llover', async () => {
    const user = userEvent.setup()
    await montar(SIN_DATOS)
    await user.click(widget())
    await user.click(screen.getByRole('switch', { name: /lluvia en el mapa/i }))

    expect(screen.queryByText(/sin precipitaciones pronosticadas/i)).toBeNull()
    expect(screen.getByText(/no significa que esté todo tranquilo/i)).toBeInTheDocument()
  })
})
