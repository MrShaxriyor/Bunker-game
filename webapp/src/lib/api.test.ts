import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  clearIdempotencyKey,
  fetchCatalog,
  fetchGiftHistory,
  fetchMe,
  getOrCreateIdempotencyKey,
  sendGift,
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

  it('reads the catalog envelope and requests the selected history page', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          items: [{ id: 5, name: 'Atirgul', description: null, price_coins: 25 }],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          items: [],
          page: 2,
          page_size: 20,
          has_more: false,
        }),
      )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchCatalog('signed-init-data')).resolves.toEqual([
      { id: 5, name: 'Atirgul', description: null, price_coins: 25 },
    ])
    await fetchGiftHistory('signed-init-data', 'received', 2, 20)

    expect(fetchMock.mock.calls[1][0]).toBe(
      `${apiBaseUrl}/api/gifts/received?page=2&page_size=20`,
    )
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
