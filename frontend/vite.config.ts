import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const apiProxyTarget = env.VITE_DEV_API_PROXY ?? 'http://localhost:8000'

  return {
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },

    server: {
      port: 5173,
      // El backend ya trae http://localhost:5173 en CORS_ORIGINS, pero pasar por
      // el proxy evita el preflight y hace que dev y produccion usen exactamente
      // la misma URL relativa (/api/v1). Un problema menos que depurar.
      proxy: {
        '/api': { target: apiProxyTarget, changeOrigin: true },
      },
    },

    build: {
      target: 'es2022',
      sourcemap: true,
      // maplibre-gl pesa cerca de 1 MB y no hay como evitarlo: es el motor de
      // render del mapa. El aviso por defecto (500 kB) solo agrega ruido.
      chunkSizeWarningLimit: 1100,
      rollupOptions: {
        output: {
          // Vite 8 usa rolldown: `manualChunks` fue reemplazado por
          // `codeSplitting`. Aislar maplibre evita invalidar toda la cache del
          // navegador cuando solo cambia el código de la aplicacion.
          codeSplitting: {
            groups: [
              { name: 'maplibre', test: /node_modules[\\/]maplibre-gl[\\/]/ },
              { name: 'query', test: /node_modules[\\/]@tanstack[\\/]/ },
            ],
          },
        },
      },
    },

    plugins: [
      react(),
      tailwindcss(),
      VitePWA({
        registerType: 'autoUpdate',
        injectRegister: 'auto',
        includeAssets: ['icons/favicon.svg', 'icons/apple-touch-icon.png'],

        manifest: {
          id: '/',
          name: 'AlertaV — Emergencias Región de Valparaíso',
          short_name: 'AlertaV',
          description:
            'Incidentes de emergencia de la Región de Valparaíso, correlacionados desde CONAF, SENAPRED y NASA FIRMS, con su nivel de confianza a la vista.',
          lang: 'es-CL',
          dir: 'ltr',
          start_url: '/',
          scope: '/',
          display: 'standalone',
          orientation: 'portrait-primary',
          background_color: '#0f172a',
          theme_color: '#0f172a',
          categories: ['utilities', 'news', 'navigation'],
          icons: [
            { src: '/icons/pwa-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
            { src: '/icons/pwa-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
            { src: '/icons/maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
          ],
        },

        workbox: {
          globPatterns: ['**/*.{js,css,html,svg,png,ico,woff2}'],
          maximumFileSizeToCacheInBytes: 4 * 1024 * 1024,
          cleanupOutdatedCaches: true,
          clientsClaim: true,
          skipWaiting: true,
          navigateFallback: '/index.html',
          navigateFallbackDenylist: [/^\/api\//, /^\/docs/, /^\/redoc/],

          runtimeCaching: [
            {
              // Incidentes: la red manda siempre. La cache solo existe para que
              // la app abra en una quebrada sin señal, y con fecha de vencimiento
              // corta: un incendio de hace una hora ya no describe el presente.
              // La UI además rotula la antiguedad (ver StalenessBanner).
              urlPattern: /\/api\/v1\/incidents\//,
              handler: 'NetworkFirst',
              options: {
                cacheName: 'alertav-incidents',
                networkTimeoutSeconds: 6,
                expiration: { maxEntries: 32, maxAgeSeconds: 60 * 10 },
                cacheableResponse: { statuses: [0, 200] },
                matchOptions: { ignoreVary: true },
              },
            },
            {
              // Tiles y sprites del mapa base: inmutables, cachear agresivo.
              urlPattern: /^https:\/\/basemaps\.cartocdn\.com\/.*/i,
              handler: 'CacheFirst',
              options: {
                cacheName: 'alertav-basemap',
                expiration: { maxEntries: 600, maxAgeSeconds: 60 * 60 * 24 * 30 },
                cacheableResponse: { statuses: [0, 200] },
              },
            },
          ],
        },

        devOptions: {
          // Permite probar el service worker con `npm run dev`.
          enabled: false,
          type: 'module',
          navigateFallback: 'index.html',
        },
      }),
    ],
  }
})
