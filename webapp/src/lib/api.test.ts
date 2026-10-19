import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  clearIdempotencyKey,
  createCatalogDiagnostics,
  fetchCatalog,
  fetchGiftHistory,
  fetchGiftInventory,
  fetchMe,
  getOrCreateIdempotencyKey,
  getSafeCatalogErrorSummary,
  shouldEnableCatalogDiagnostics,
  sendGift,
  sellGiftInventoryItem,
} from './api'

const jsonResponse = (payload: unknown, status = 200) =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim().replace(/\/$/, '') ?? ''

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Para Gifts API client', () => {
  it('sends initData in the backend Authorization format', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        telegram_user_id: 101,
        balance_coins: 250,
        active_para: { para_id: 71, partner_display_name: 'Aziza' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const profile = await fetchMe('signed-init-data')

    expect(profile.balance_coins).toBe(250)
    expect(fetchMock).toHaveBeenCalledWith(
      `${apiBaseUrl}/api/me`,
      expect.objectContaining({
        headers: { Authorization: 'tma signed-init-data' },
      }),
    )
  })

  it('reads a seven-item catalog and records the HTTP status and safe request headers', async () => {
    const items = Array.from({ length: 7 }, (_, index) => ({
      id: index + 1,
      name: `Gift ${index + 1}`,
      description: null,
      price_coins: (index + 1) * 50,
    }))
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ items }, 200))
      .mockResolvedValueOnce(
        jsonResponse({
          items: [],
          page: 2,
          page_size: 20,
          has_more: false,
        }),
      )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchCatalog('signed-init-data')).resolves.toEqual({ items, status: 200 })
    await fetchGiftHistory('signed-init-data', 'received', 2, 20)

    expect(fetchMock.mock.calls[0][0]).toBe(`${apiBaseUrl}/api/gifts/catalog`)
    expect(fetchMock.mock.calls[0][1].method ?? 'GET').toBe('GET')
    expect(fetchMock.mock.calls[0][1].headers).toEqual({
      Authorization: 'tma signed-init-data',
    })
    expect(fetchMock.mock.calls[1][0]).toBe(
      `${apiBaseUrl}/api/gifts/received?page=2&page_size=20`,
    )
  })

  it('preserves a successful empty catalog as a distinct zero-item result', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ items: [] }, 200)))

    await expect(fetchCatalog('signed-init-data')).resolves.toEqual({
      items: [],
      status: 200,
    })
  })

  it.each([
    [401, 'Telegram sessiyasi yaroqsiz yoki muddati tugagan.'],
    [403, 'API so‘roviga ruxsat berilmadi.'],
    [503, 'API yoki ma’lumotlar bazasi vaqtincha ishlamayapti.'],
  ])('summarizes HTTP %i without exposing the raw server detail', async (status, summary) => {
    const rawDetail = 'secret-token-and-init-data-must-not-appear'
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ detail: rawDetail }, status)),
    )

    let caughtError: unknown
    try {
      await fetchCatalog('signed-init-data')
    } catch (error) {
      caughtError = error
    }

    expect(caughtError).toBeInstanceOf(ApiError)
    expect(caughtError).toMatchObject({ status, detail: rawDetail })
    const safeSummary = getSafeCatalogErrorSummary(caughtError)
    expect(safeSummary).toBe(summary)
    expect(safeSummary).not.toContain(rawDetail)
  })

  it('uses a fixed summary for network errors', () => {
    expect(getSafeCatalogErrorSummary(new TypeError('URL query secret=hidden'))).toBe(
      'API bilan tarmoq aloqasi o‘rnatilmadi.',
    )
  })

  it('reports only whether initData exists and never includes its value', () => {
    const initData = 'sensitive-init-data-value'
    const present = createCatalogDiagnostics(initData, { status: 200, itemCount: 7, source: 'api' })
    const absent = createCatalogDiagnostics('', { source: 'not_requested' })

    expect(present.initDataPresent).toBe(true)
    expect(absent.initDataPresent).toBe(false)
    expect(JSON.stringify(present)).not.toContain(initData)
    expect(present).toMatchObject({ status: 200, itemCount: 7 })
  })

  it('cannot enable diagnostics in a production build even when the flag is true', () => {
    expect(shouldEnableCatalogDiagnostics(true, 'true')).toBe(true)
    expect(shouldEnableCatalogDiagnostics(true, 'false')).toBe(false)
    expect(shouldEnableCatalogDiagnostics(false, 'true')).toBe(false)
  })

  it('sends only the backend gift request properties', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        gift_id: 88,
        balance_coins: 225,
        replayed: false,
        message: 'Sovg‘a muvaffaqiyatli yuborildi.',
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await sendGift('signed-init-data', 5, 'retry-key')

    expect(fetchMock.mock.calls[0]).toMatchObject([
      `${apiBaseUrl}/api/gifts/send`,
      {
        method: 'POST',
        body: JSON.stringify({
          shop_item_id: 5,
          idempotency_key: 'retry-key',
        }),
      },
    ])
  })

  it('loads only the authenticated user inventory through the API client', async () => {
    const item = {
      id: 9,
      sender_id: 101,
      recipient_id: 202,
      sender_name: 'Sender',
      recipient_name: 'Partner',
      gift_name: '🌹 Rose',
      image_emoji: '🌹',
      received_at: '2026-10-19T10:00:00Z',
      original_price_coins: 300,
      resale_price_coins: 150,
      sold_at: null,
      status: 'owned',
    }
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ items: [item] }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchGiftInventory('signed-init-data')).resolves.toEqual({ items: [item] })
    expect(fetchMock).toHaveBeenCalledWith(
      `${apiBaseUrl}/api/gifts/inventory`,
      expect.objectContaining({ headers: { Authorization: 'tma signed-init-data' } }),
    )
  })

  it('sells an inventory copy via the authenticated backend endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      item: {
        id: 9,
        sender_id: 101,
        recipient_id: 202,
        sender_name: 'Sender',
        recipient_name: 'Partner',
        gift_name: '🌹 Rose',
        image_emoji: '🌹',
        received_at: '2026-10-19T10:00:00Z',
        original_price_coins: 300,
        resale_price_coins: 150,
        sold_at: '2026-10-19T11:00:00Z',
        status: 'sold',
      },
      balance_coins: 150,
    }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await sellGiftInventoryItem('signed-init-data', 9)
    expect(result.balance_coins).toBe(150)
    expect(fetchMock).toHaveBeenCalledWith(
      `${apiBaseUrl}/api/gifts/inventory/9/sell`,
      expect.objectContaining({
        method: 'POST',
        headers: { Authorization: 'tma signed-init-data' },
      }),
    )
  })

  it('reuses the pending idempotency key for retries until it is cleared', () => {
    const values = new Map<string, string>()
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    }
    const randomUUID = vi.fn(() => 'stable-retry-key')
    vi.stubGlobal('sessionStorage', storage)
    vi.stubGlobal('crypto', { randomUUID })

    const firstTry = getOrCreateIdempotencyKey(101, 5)
    const retry = getOrCreateIdempotencyKey(101, 5)
    expect(retry).toBe(firstTry)
    expect(randomUUID).toHaveBeenCalledTimes(1)

    clearIdempotencyKey(101, 5)
    expect(getOrCreateIdempotencyKey(101, 5)).toBe('stable-retry-key')
    expect(randomUUID).toHaveBeenCalledTimes(2)
  })

  it('exposes safe backend error status and detail', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: 'There are not enough coins for this gift.' }, 409),
      ),
    )

    const failedRequest = fetchMe('signed-init-data')
    await expect(failedRequest).rejects.toBeInstanceOf(ApiError)
    await expect(failedRequest).rejects.toMatchObject({
      status: 409,
      detail: 'There are not enough coins for this gift.',
    })
  })
})
