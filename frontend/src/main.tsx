import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { queryClient } from './lib/queryClient'
// Pone el nivel de cristal en <html> antes del primer pintado (§D).
import './lib/glassStore'
// Guarda el aviso de instalación de Chrome aunque llegue antes que React (§Q).
import './lib/install'
import './index.css'

const container = document.getElementById('root')
if (!container) throw new Error('No se encontró #root en index.html')

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
