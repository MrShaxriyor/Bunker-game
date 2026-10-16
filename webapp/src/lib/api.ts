export interface Me {
  telegram_user_id: number
  balance_coins: number
  active_para: {
    para_id: number
    partner_display_name: string
  } | null
}

export interface ShopGift {
  id: number
  name: string
  description: string | null
  price_coins: number
}

export interface GiftHistoryRecord {
  id: number
  para_id: number
  gift_name: string
  price_coins: number
  created_at: string
}

export interface GiftHistoryPage {
  items: GiftHistoryRecord[]
  page: number
  page_size: number
  has_more: boolean
}

export interface GiftSendResponse {
  gift_id: number
  balance_coins: number
  replayed: boolean
  message: string
}

export type Tab = 'home' | 'shop' | 'history'

interface ApiErrorBody {
  detail?: unknown
}

export class ApiError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

function getApiBaseUrl() {
  const configured = import.meta.env.VITE_API_BASE_URL?.trim()
  if (!configured) return ''
  const url = new URL(configured)
  const isLocal = url.hostname === 'localhost' || url.hostname === '127.0.0.1'
  if (url.protocol !== 'https:' && !(import.meta.env.DEV && isLocal)) {
    throw new Error('Para Gifts API HTTPS ulanishidan foydalanishi kerak.')
  }
  return url.toString().replace(/\/$/, '')
}

async function request<T>(
  path: string,
  initData: string,
  options: RequestInit = {},
): Promise<T> {
  if (!initData) throw new Error('Telegram initData topilmadi.')
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    ...options,
    headers: {
      Authorization: `tma ${initData}`,
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
  })

  if (!response.ok) {
    let detail = ''
    try {
      const body = (await response.json()) as ApiErrorBody
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      detail = ''
    }
    throw new ApiError(response.status, detail)
  }
  return (await response.json()) as T
}

export function fetchMe(initData: string) {
  return request<Me>('/api/me', initData)
}

export async function fetchCatalog(initData: string) {
  const response = await request<{ items: ShopGift[] }>(
    '/api/gifts/catalog',
    initData,
  )
  return response.items
}

export function fetchGiftHistory(
  initData: string,
  mode: 'received' | 'sent',
  page: number,
  pageSize: number,
) {
  const query = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  return request<GiftHistoryPage>(
    `/api/gifts/${mode}?${query.toString()}`,
    initData,
  )
}

export function sendGift(
  initData: string,
  shopItemId: number,
  idempotencyKey: string,
) {
  return request<GiftSendResponse>('/api/gifts/send', initData, {
    method: 'POST',
    body: JSON.stringify({
      shop_item_id: shopItemId,
      idempotency_key: idempotencyKey,
    }),
  })
}

function idempotencyStorageKey(telegramUserId: number, shopItemId: number) {
  return `para-gifts:pending:${telegramUserId}:${shopItemId}`
}

export function getOrCreateIdempotencyKey(
  telegramUserId: number,
  shopItemId: number,
) {
  const storageKey = idempotencyStorageKey(telegramUserId, shopItemId)
  const existing = sessionStorage.getItem(storageKey)
  if (existing) return existing

  const idempotencyKey = crypto.randomUUID()
  sessionStorage.setItem(storageKey, idempotencyKey)
  return idempotencyKey
}

export function clearIdempotencyKey(
  telegramUserId: number,
  shopItemId: number,
) {
  sessionStorage.removeItem(idempotencyStorageKey(telegramUserId, shopItemId))
}
