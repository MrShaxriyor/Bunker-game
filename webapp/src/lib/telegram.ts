export interface TelegramWebApp {
  initData: string
  ready: () => void
  expand: () => void
  setHeaderColor?: (color: string) => void
  setBackgroundColor?: (color: string) => void
}

declare global {
  interface Window {
    Telegram?: {
      WebApp?: TelegramWebApp
    }
  }
}

export function getTelegramWebApp() {
  if (typeof window === 'undefined') return undefined
  return window.Telegram?.WebApp
}
