import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { getTelegramWebApp } from './lib/telegram'

const telegramWebApp = getTelegramWebApp()
telegramWebApp?.ready()
telegramWebApp?.expand()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
