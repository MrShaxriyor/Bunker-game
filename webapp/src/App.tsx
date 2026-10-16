import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import {
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronRight,
  CircleHelp,
  Coins,
  Gift,
  Heart,
  Home,
  LoaderCircle,
  LockKeyhole,
  RefreshCw,
  Send,
  Sparkles,
  Store,
  X,
} from 'lucide-react'
import {
  ApiError,
  clearIdempotencyKey,
  fetchCatalog,
  fetchGiftHistory,
  fetchMe,
  getOrCreateIdempotencyKey,
  sendGift,
} from './lib/api'
import type {
  GiftHistoryPage,
  GiftHistoryRecord,
  GiftSendResponse,
  Me,
  ShopGift,
  Tab,
} from './lib/api'
import { getTelegramWebApp } from './lib/telegram'
import './App.css'

const PAGE_SIZE = 20
const GIFT_EMOJIS = ['🌹', '🎁', '💌', '🧸', '💐', '✨']
const UZBEK_MONTHS = [
  'yanvar',
  'fevral',
  'mart',
  'aprel',
  'may',
  'iyun',
  'iyul',
  'avgust',
  'sentabr',
  'oktabr',
  'noyabr',
  'dekabr',
]

const demoGifts: ShopGift[] = [
  { id: 1, name: 'Atirgul', description: 'Bir dona nafis atirgul.', price_coins: 25 },
  { id: 2, name: 'Sirli maktub', description: 'Yurakdan chiqqan kichik xat.', price_coins: 40 },
  { id: 3, name: 'Yumshoq ayiqcha', description: 'Har doim yoningizda bo‘ladigan sovg‘a.', price_coins: 75 },
  { id: 4, name: 'Bahor guldastasi', description: 'Kayfiyatga bahor olib kiring.', price_coins: 110 },
]

const demoHistory: GiftHistoryRecord[] = [
  {
    id: 1,
    para_id: 71,
    gift_name: 'Atirgul',
    price_coins: 25,
    created_at: new Date(Date.now() - 1000 * 60 * 60 * 3).toISOString(),
  },
  {
    id: 2,
    para_id: 71,
    gift_name: 'Sirli maktub',
    price_coins: 40,
    created_at: new Date(Date.now() - 1000 * 60 * 60 * 24).toISOString(),
  },
]

function formatCoins(value: number) {
  return new Intl.NumberFormat('uz-UZ').format(value)
}

function formatDate(value: string) {
  const date = new Date(value)
  const time = new Intl.DateTimeFormat('uz-Latn-UZ', {
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
  return `${date.getDate()}-${UZBEK_MONTHS[date.getMonth()]}, ${time}`
}

function giftEmoji(gift: ShopGift) {
  const name = gift.name.toLocaleLowerCase('uz-Latn-UZ')
  if (name.includes('guldasta')) return '💐'
  if (name.includes('atirgul') || name.includes('gul')) return '🌹'
  if (name.includes('maktub') || name.includes('xat')) return '💌'
  if (name.includes('ayiq')) return '🧸'
  return GIFT_EMOJIS[gift.id % GIFT_EMOJIS.length]
}

function getErrorMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'Telegram sessiyasi tugagan. Ilovani qayta oching.'
    if (error.status === 404) return 'Bu sovg‘a hozir mavjud emas.'
    if (error.status === 409) {
      const detail = error.detail.toLocaleLowerCase('en-US')
      if (detail.includes('coins')) return 'Coin balansingiz bu sovg‘a uchun yetarli emas.'
      if (detail.includes('para')) return 'Sovg‘a yuborish uchun faol para juftingiz bo‘lishi kerak.'
      return 'Bu amalni bajarib bo‘lmadi. Ma’lumotlarni yangilab ko‘ring.'
    }
    if (error.status === 422) return 'So‘rov ma’lumotlari noto‘g‘ri. Qayta urinib ko‘ring.'
    if (error.status >= 500) return 'Server vaqtincha ishlamayapti. Birozdan keyin yana urinib ko‘ring.'
    return 'So‘rovni bajarib bo‘lmadi. Qayta urinib ko‘ring.'
  }
  if (error instanceof TypeError) return 'Internet bilan aloqa yo‘q. Ulanishni tekshiring.'
  return 'Kutilmagan xatolik yuz berdi. Qayta urinib ko‘ring.'
}

function App() {
  const webApp = getTelegramWebApp()
  const initData = webApp?.initData ?? ''
  const isDemo = import.meta.env.DEV && !initData
  const [activeTab, setActiveTab] = useState<Tab>('home')
  const [profile, setProfile] = useState<Me | null>(null)
  const [gifts, setGifts] = useState<ShopGift[]>([])
  const [catalogLoading, setCatalogLoading] = useState(true)
  const [profileLoading, setProfileLoading] = useState(true)
  const [catalogError, setCatalogError] = useState('')
  const [profileError, setProfileError] = useState('')
  const [selectedGift, setSelectedGift] = useState<ShopGift | null>(null)
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState('')
  const [sendResult, setSendResult] = useState<GiftSendResponse | null>(null)
  const [historyMode, setHistoryMode] = useState<'received' | 'sent'>('received')
  const [history, setHistory] = useState<GiftHistoryPage | null>(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const historyRequestId = useRef(0)

  useEffect(() => {
    webApp?.ready()
    webApp?.expand()
  }, [webApp])

  const loadProfile = useCallback(async () => {
    if (isDemo) {
      setProfile({
        telegram_user_id: 0,
        balance_coins: 680,
        active_para: { para_id: 71, partner_display_name: 'Aziza' },
      })
      setProfileError('')
      setProfileLoading(false)
      return
    }
    if (!initData) {
      setProfileError('Ilovani Telegram ichidan oching — xavfsiz ulanish talab qilinadi.')
      setProfileLoading(false)
      return
    }
    setProfileLoading(true)
    setProfileError('')
    try {
      setProfile(await fetchMe(initData))
    } catch (error) {
      setProfileError(getErrorMessage(error))
    } finally {
      setProfileLoading(false)
    }
  }, [initData, isDemo])

  const loadCatalog = useCallback(async () => {
    if (isDemo) {
      setGifts(demoGifts)
      setCatalogError('')
      setCatalogLoading(false)
      return
    }
    if (!initData) {
      setCatalogError('Katalogni ko‘rish uchun ilovani Telegram ichidan oching.')
      setCatalogLoading(false)
      return
    }
    setCatalogLoading(true)
    setCatalogError('')
    try {
      setGifts(await fetchCatalog(initData))
    } catch (error) {
      setCatalogError(getErrorMessage(error))
    } finally {
      setCatalogLoading(false)
    }
  }, [initData, isDemo])

  useEffect(() => {
    // eslint-disable-next-line react/set-state-in-effect
    void loadProfile()
    // eslint-disable-next-line react/set-state-in-effect
    void loadCatalog()
  }, [loadCatalog, loadProfile])

  const loadHistory = useCallback(async (mode: 'received' | 'sent') => {
    const requestId = ++historyRequestId.current
    setHistoryLoading(true)
    setHistoryError('')
    try {
      const result = isDemo
        ? {
            items: demoHistory,
            page: 1,
            page_size: PAGE_SIZE,
            has_more: false,
          }
        : initData
          ? await fetchGiftHistory(initData, mode, 1, PAGE_SIZE)
          : null
      if (requestId !== historyRequestId.current) return
      if (!result) {
        setHistoryError('Tarixni ko‘rish uchun ilovani Telegram ichidan oching.')
        setHistory(null)
      } else {
        setHistory(result)
      }
    } catch (error) {
      if (requestId === historyRequestId.current) {
        setHistoryError(getErrorMessage(error))
        setHistory(null)
      }
    } finally {
      if (requestId === historyRequestId.current) setHistoryLoading(false)
    }
  }, [initData, isDemo])

  const featuredGift = gifts[0]
  const greetingName = profile?.active_para?.partner_display_name
  const openGift = (gift: ShopGift) => {
    setSelectedGift(gift)
    setSendError('')
    setSendResult(null)
  }

  const closeGiftDialog = () => {
    if (sending) return
    setSelectedGift(null)
    setSendError('')
    setSendResult(null)
  }

  const handleSendGift = async () => {
    if (!selectedGift || sending) return
    setSending(true)
    setSendError('')
    const idempotencyKey = isDemo
      ? `demo-${selectedGift.id}-${Date.now()}`
      : getOrCreateIdempotencyKey(profile?.telegram_user_id ?? 0, selectedGift.id)
    try {
      if (isDemo) {
        const result: GiftSendResponse = {
          gift_id: Date.now(),
          balance_coins: profile?.balance_coins ?? 0,
          replayed: false,
          message: 'Demo rejimi — haqiqiy sovg‘a yuborilmadi.',
        }
        setSendResult(result)
      } else {
        const result = await sendGift(initData, selectedGift.id, idempotencyKey)
        clearIdempotencyKey(profile?.telegram_user_id ?? 0, selectedGift.id)
        setProfile((current) => current && { ...current, balance_coins: result.balance_coins })
        setSendResult(result)
      }
      setHistory(null)
      void loadHistory(historyMode)
    } catch (error) {
      if (error instanceof ApiError && error.status < 500) {
        clearIdempotencyKey(profile?.telegram_user_id ?? 0, selectedGift.id)
      }
      setSendError(getErrorMessage(error))
    } finally {
      setSending(false)
    }
  }

  const loadMoreHistory = async () => {
    if (!history?.has_more || historyLoading || !initData || isDemo) return
    const requestId = ++historyRequestId.current
    const requestedMode = historyMode
    setHistoryLoading(true)
    setHistoryError('')
    try {
      const nextPage = await fetchGiftHistory(
        initData,
        requestedMode,
        history.page + 1,
        PAGE_SIZE,
      )
      if (requestId !== historyRequestId.current) return
      setHistory((current) =>
        current
          ? {
              ...nextPage,
              items: [...current.items, ...nextPage.items],
            }
          : nextPage,
      )
    } catch (error) {
      if (requestId === historyRequestId.current) {
        setHistoryError(getErrorMessage(error))
      }
    } finally {
      if (requestId === historyRequestId.current) setHistoryLoading(false)
    }
  }

  const contentTitle = useMemo(() => {
    if (activeTab === 'shop') return 'Sovg‘alar do‘koni'
    if (activeTab === 'history') return 'Sovg‘alar tarixi'
    return 'Bosh sahifa'
  }, [activeTab])

  return (
    <div className="app-shell mx-auto min-h-screen">
      <div className="ambient ambient-one" aria-hidden="true" />
      <div className="ambient ambient-two" aria-hidden="true" />
      <header className="topbar flex items-center justify-between">
        <button
          className="brand"
          type="button"
          onClick={() => setActiveTab('home')}
          aria-label="Bosh sahifa"
        >
          <span className="brand-mark"><Heart size={19} fill="currentColor" /></span>
          <span className="brand-name">para<span>gift</span></span>
        </button>
        <div className="topbar-right">
          {isDemo && <span className="demo-pill">DEMO</span>}
          <button
            className="icon-button"
            type="button"
            aria-label="Sahifani yangilash"
            onClick={() => {
              void loadProfile()
              void loadCatalog()
              if (activeTab === 'history') {
                setHistoryLoading(true)
                setHistoryError('')

              }
            }}
          >
            <RefreshCw size={17} />
          </button>
          <div className="avatar">{greetingName?.slice(0, 1).toUpperCase() ?? '♡'}</div>
        </div>
      </header>

      <main className="main-content mx-auto w-full">
        <div className="page-heading">
          <div>
            <span className="eyebrow"><Sparkles size={13} /> BUNKER TG · PARA GIFTS</span>
            <h1>{contentTitle}</h1>
          </div>
          <span className="secure-tag"><LockKeyhole size={13} /> Xavfsiz</span>
        </div>

        {activeTab === 'home' && (
          <HomePage
            profile={profile}
            loading={profileLoading}
            error={profileError}
            onRetry={() => void loadProfile()}
            onShop={() => setActiveTab('shop')}
            onHistory={() => {
              void loadHistory(historyMode)
              setActiveTab('history')
            }}
            featuredGift={featuredGift}
            onGift={openGift}
          />
        )}
        {activeTab === 'shop' && (
          <ShopPage
            gifts={gifts}
            loading={catalogLoading}
            error={catalogError}
            onRetry={() => void loadCatalog()}
            onGift={openGift}
          />
        )}
        {activeTab === 'history' && (
          <HistoryPage
            historyMode={historyMode}
            onModeChange={(mode) => {
              setHistoryMode(mode)
              setHistory(null)
              void loadHistory(mode)
            }}
            history={history}
            loading={historyLoading}
            error={historyError}
            onRetry={() => void loadHistory(historyMode)}
            onLoadMore={() => void loadMoreHistory()}
            demo={isDemo}
          />
        )}
      </main>

      <nav className="bottom-nav" aria-label="Asosiy navigatsiya">
        <NavButton active={activeTab === 'home'} label="Bosh sahifa" onClick={() => setActiveTab('home')}>
          <Home size={20} />
        </NavButton>
        <NavButton active={activeTab === 'shop'} label="Do‘kon" onClick={() => setActiveTab('shop')}>
          <Store size={20} />
        </NavButton>
        <NavButton active={activeTab === 'history'} label="Tarix" onClick={() => {
          void loadHistory(historyMode)
          setActiveTab('history')
        }}>
          <Gift size={20} />
        </NavButton>
      </nav>

      {selectedGift && (
        <GiftDialog
          gift={selectedGift}
          balance={profile?.balance_coins ?? 0}
          sending={sending}
          error={sendError}
          result={sendResult}
          onClose={closeGiftDialog}
          onConfirm={() => void handleSendGift()}
          demo={isDemo}
        />
      )}
    </div>
  )
}

function NavButton({
  active,
  label,
  onClick,
  children,
}: {
  active: boolean
  label: string
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      className={`nav-item${active ? ' nav-item-active' : ''}`}
      type="button"
      aria-current={active ? 'page' : undefined}
      onClick={onClick}
    >
      {children}
      <span>{label}</span>
    </button>
  )
}

function HomePage({
  profile,
  loading,
  error,
  onRetry,
  onShop,
  onHistory,
  featuredGift,
  onGift,
}: {
  profile: Me | null
  loading: boolean
  error: string
  onRetry: () => void
  onShop: () => void
  onHistory: () => void
  featuredGift?: ShopGift
  onGift: (gift: ShopGift) => void
}) {
  return (
    <div className="home-layout">
      <section className="welcome-copy">
        <p className="welcome-kicker">Mehrningizga mehr qo‘shing</p>
        <h2>Yaqin insoningizni <span>xursand qiling.</span></h2>
        <p className="welcome-text">Kichik sovg‘a — katta tabassum. O‘zingizga yoqqanini tanlang va para juftingizga yuboring.</p>
      </section>

      {loading ? (
        <LoadingCard label="Profil ma’lumotlari yuklanmoqda..." />
      ) : error ? (
        <ErrorCard message={error} onRetry={onRetry} />
      ) : (
        <section className="balance-card">
          <div className="balance-top">
            <span className="balance-label"><Coins size={16} /> BUNKER COIN BALANSI</span>
            <span className="balance-orbit"><Sparkles size={16} /></span>
          </div>
          <div className="balance-amount">{formatCoins(profile?.balance_coins ?? 0)} <span>coin</span></div>
          <div className="balance-divider" />
          <div className="para-row">
            <div className="para-heart"><Heart size={17} fill="currentColor" /></div>
            <div className="para-copy">
              <span className="para-label">Sizning para juftingiz</span>
              <strong>{profile?.active_para?.partner_display_name ?? 'Hozircha juftingiz yo‘q'}</strong>
            </div>
            <span className={`status-dot${profile?.active_para ? '' : ' status-dot-muted'}`} />
          </div>
          {!profile?.active_para && <p className="para-hint">Faol para juftingiz bo‘lmasa, sovg‘a yuborib bo‘lmaydi.</p>}
        </section>
      )}

      <div className="section-title-row">
        <div>
          <span className="section-eyebrow">YURAKDAN TANLANGAN</span>
          <h3>Bugungi tavsiya</h3>
        </div>
        <button className="text-link" type="button" onClick={onShop}>Barchasi <ArrowRight size={15} /></button>
      </div>

      {featuredGift ? (
        <button className="featured-card" type="button" onClick={() => onGift(featuredGift)}>
          <GiftArtwork gift={featuredGift} index={0} />
          <span className="featured-info">
            <span className="featured-badge"><Sparkles size={12} /> KO‘P TANLANADI</span>
            <strong>{featuredGift.name}</strong>
            <span className="featured-price"><Coins size={15} /> {formatCoins(featuredGift.price_coins)} coin</span>
          </span>
          <span className="featured-arrow"><ChevronRight size={19} /></span>
        </button>
      ) : (
        <button className="explore-card" type="button" onClick={onShop}>
          <span className="explore-icon"><Gift size={20} /></span>
          <span><strong>Sovg‘alar olami</strong><small>Do‘konni ko‘rib chiqing</small></span>
          <ArrowRight size={18} />
        </button>
      )}

      <div className="quick-links">
        <button type="button" onClick={onShop}><span className="quick-icon quick-purple"><Store size={17} /></span><span><strong>Sovg‘a tanlash</strong><small>Do‘konni ochish</small></span><ArrowRight size={16} /></button>
        <button type="button" onClick={onHistory}><span className="quick-icon quick-pink"><Heart size={17} /></span><span><strong>Sovg‘alar tarixi</strong><small>Yuborilgan va olingan</small></span><ArrowRight size={16} /></button>
      </div>
    </div>
  )
}

function ShopPage({
  gifts,
  loading,
  error,
  onRetry,
  onGift,
}: {
  gifts: ShopGift[]
  loading: boolean
  error: string
  onRetry: () => void
  onGift: (gift: ShopGift) => void
}) {
  if (loading) return <LoadingCard label="Sovg‘alar yuklanmoqda..." />
  if (error) return <ErrorCard message={error} onRetry={onRetry} />
  if (!gifts.length) {
    return (
      <EmptyState
        icon={<Gift size={27} />}
        title="Hozircha sovg‘alar yo‘q"
        description="Yangi sovg‘alar paydo bo‘lsa, shu yerda ko‘rasiz."
      />
    )
  }

  return (
    <div className="shop-page">
      <div className="shop-intro">
        <span className="intro-spark"><Sparkles size={17} /></span>
        <p>Har bir sovg‘ada bir chimdim mehr bor. O‘zingizga yoqqanini tanlang.</p>
      </div>
      <div className="gift-grid">
        {gifts.map((gift, index) => (
          <GiftCard
            key={gift.id}
            gift={gift}
            index={index}
            onGift={() => onGift(gift)}
          />
        ))}
      </div>
    </div>
  )
}

function GiftArtwork({ gift, index }: { gift: ShopGift; index: number }) {
  return (
    <span className={`gift-art gift-art-${index % 4}`} aria-hidden="true">
      <span className="gift-glow" />
      <span className="gift-emoji">{giftEmoji(gift)}</span>
      <Sparkles className="gift-spark gift-spark-one" size={14} />
      <Sparkles className="gift-spark gift-spark-two" size={10} />
    </span>
  )
}

function GiftCard({
  gift,
  index,
  onGift,
}: {
  gift: ShopGift
  index: number
  onGift: () => void
}) {
  return (
    <article className="gift-card">
      <GiftArtwork gift={gift} index={index} />
      <div className="gift-card-body">
        <span className="gift-card-overline">PARA UCHUN</span>
        <h3>{gift.name}</h3>
        <p>{gift.description || 'Yaqin insoningiz uchun alohida sovg‘a.'}</p>
        <div className="gift-card-footer">
          <span className="coin-price"><Coins size={14} /> {formatCoins(gift.price_coins)}</span>
          <button
            className="send-button"
            type="button"
            aria-label={`${gift.name} sovg‘asini yuborish`}
            onClick={onGift}
          >
            <Send size={15} />
          </button>
        </div>
      </div>
    </article>
  )
}

function HistoryPage({
  historyMode,
  onModeChange,
  history,
  loading,
  error,
  onRetry,
  onLoadMore,
  demo,
}: {
  historyMode: 'received' | 'sent'
  onModeChange: (mode: 'received' | 'sent') => void
  history: GiftHistoryPage | null
  loading: boolean
  error: string
  onRetry: () => void
  onLoadMore: () => void
  demo: boolean
}) {
  return (
    <section className="history-page">
      <div className="history-tabs" role="tablist" aria-label="Sovg‘alar yo‘nalishi">
        <button
          type="button"
          role="tab"
          aria-selected={historyMode === 'received'}
          className={historyMode === 'received' ? 'history-tab selected' : 'history-tab'}
          onClick={() => onModeChange('received')}
        >
          <ArrowDownLeft size={16} /> Qabul qilingan
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={historyMode === 'sent'}
          className={historyMode === 'sent' ? 'history-tab selected' : 'history-tab'}
          onClick={() => onModeChange('sent')}
        >
          <ArrowUpRight size={16} /> Yuborilgan
        </button>
      </div>
      {loading && !history ? (
        <LoadingCard label="Sovg‘alar tarixi yuklanmoqda..." />
      ) : error ? (
        <ErrorCard message={error} onRetry={onRetry} />
      ) : !history?.items.length ? (
        <EmptyState
          icon={<Heart size={26} />}
          title={historyMode === 'received' ? 'Hali sovg‘a olmagansiz' : 'Hali sovg‘a yubormagansiz'}
          description="Har bir go‘zal voqea birinchi sovg‘adan boshlanadi."
        />
      ) : (
        <>
          <div className="history-list">
            {history.items.map((item) => (
              <HistoryCard key={item.id} item={item} mode={historyMode} />
            ))}
          </div>
          {history.has_more && (
            <button
              className="load-more-button"
              type="button"
              disabled={loading || demo}
              onClick={onLoadMore}
            >
              {loading ? <LoaderCircle className="spin" size={17} /> : null}
              Yana ko‘rish
            </button>
          )}
        </>
      )}
    </section>
  )
}

function HistoryCard({
  item,
  mode,
}: {
  item: GiftHistoryRecord
  mode: 'received' | 'sent'
}) {
  return (
    <article className="history-card">
      <span className="history-gift-icon"><Gift size={20} /></span>
      <div className="history-details">
        <strong>{item.gift_name}</strong>
        <span>{mode === 'received' ? 'Sizga yuborildi' : 'Siz yubordingiz'} · {formatDate(item.created_at)}</span>
      </div>
      <div className="history-price">
        <span>{formatCoins(item.price_coins)}</span>
        <small>coin</small>
      </div>
    </article>
  )
}

function LoadingCard({ label }: { label: string }) {
  return (
    <div className="state-card loading-card" role="status">
      <LoaderCircle className="spin" size={22} />
      <span>{label}</span>
    </div>
  )
}

function ErrorCard({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="state-card error-card" role="alert">
      <CircleHelp size={22} />
      <p>{message}</p>
      <button type="button" onClick={onRetry}><RefreshCw size={14} /> Qayta urinish</button>
    </div>
  )
}

function EmptyState({
  icon,
  title,
  description,
}: {
  icon: ReactNode
  title: string
  description: string
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon">{icon}</span>
      <h3>{title}</h3>
      <p>{description}</p>
    </div>
  )
}

function GiftDialog({
  gift,
  balance,
  sending,
  error,
  result,
  onClose,
  onConfirm,
  demo,
}: {
  gift: ShopGift
  balance: number
  sending: boolean
  error: string
  result: GiftSendResponse | null
  onClose: () => void
  onConfirm: () => void
  demo: boolean
}) {
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !sending) onClose()
    }}>
      <section
        className="gift-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="gift-dialog-title"
      >
        <button className="dialog-close" type="button" aria-label="Yopish" disabled={sending} onClick={onClose}>
          <X size={19} />
        </button>
        {result ? (
          <div className="dialog-result">
            <span className="success-mark"><Check size={28} /></span>
            <span className="dialog-eyebrow">{demo ? 'XAVFSIZ DEMO' : 'MEHR YETKAZILDI'}</span>
            <h2 id="gift-dialog-title">{demo ? 'Demo ko‘rinishi' : 'Sovg‘a yuborildi!'}</h2>
            <p>{demo ? 'Bu sinov ko‘rinishi. Hech qanday so‘rov yoki coin o‘zgarishi bo‘lmadi.' : `${gift.name} para juftingizga yuborildi. Chiroyli lahzalar ko‘p bo‘lsin!`}</p>
            <div className="dialog-new-balance"><Coins size={16} /> Yangi balans <strong>{formatCoins(result.balance_coins)} coin</strong></div>
            <button className="primary-button dialog-primary" type="button" onClick={onClose}>Ajoyib <Check size={16} /></button>
          </div>
        ) : (
          <>
            <div className="dialog-art-wrap"><GiftArtwork gift={gift} index={2} /></div>
            <span className="dialog-eyebrow">KICHIK E’TIBOR, KATTA TABASSUM</span>
            <h2 id="gift-dialog-title">Sovg‘ani yuborasizmi?</h2>
            <p className="dialog-description">“{gift.name}” para juftingizga yuboriladi.</p>
            <div className="confirmation-row">
              <span><Coins size={16} /> Sovg‘a narxi</span>
              <strong>{formatCoins(gift.price_coins)} coin</strong>
            </div>
            <div className="confirmation-row balance-confirmation">
              <span>Balansingiz</span>
              <strong>{formatCoins(balance)} coin</strong>
            </div>
            {error && <p className="dialog-error" role="alert">{error}</p>}
            {demo && <p className="demo-notice">Demo rejimida amallar haqiqiy API’ga yuborilmaydi.</p>}
            <button
              className="primary-button dialog-primary"
              type="button"
              disabled={sending}
              onClick={onConfirm}
            >
              {sending ? <><LoaderCircle className="spin" size={17} /> Yuborilmoqda...</> : <><Send size={16} /> Sovg‘ani yuborish</>}
            </button>
            <button className="secondary-button" type="button" disabled={sending} onClick={onClose}>Bekor qilish</button>
          </>
        )}
      </section>
    </div>
  )
}

function getAppStartMessage(hasInitData: boolean, demo: boolean) {
  if (demo) return null
  if (!hasInitData) return 'Bu sahifa faqat Telegram ichida ochiladi.'
  return null
}

export default function AppRoot() {
  const webApp = getTelegramWebApp()
  const demo = import.meta.env.DEV && !webApp?.initData
  const startMessage = getAppStartMessage(Boolean(webApp?.initData), demo)

  if (startMessage) {
    return (
      <div className="app-shell launch-screen">
        <div className="launch-card">
          <span className="launch-icon"><Heart size={27} fill="currentColor" /></span>
          <h1>Telegram ichida oching</h1>
          <p>Para Gifts xavfsiz ishlashi uchun Telegram WebApp orqali ochilishi kerak.</p>
          <span className="secure-tag"><LockKeyhole size={13} /> Telegram orqali himoyalangan</span>
        </div>
      </div>
    )
  }
  return <App />
}
