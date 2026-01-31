import { useState, useEffect } from 'react'
import PaperTrading from './PaperTrading'

interface Stats {
  total_bets: number
  wins: number
  losses: number
  total_wagered: number
  total_pnl: number
  win_rate: number
}

interface Position {
  id: number
  market_id: string
  market_name: string
  asset: string
  side: string
  entry_price: number
  amount_usd: number
  shares: number
  target_price: number
  start_time: string
  end_time: string
  status: string
  exit_price: number | null
  pnl: number | null
  resolved_at: string | null
  created_at: string
  current_price?: number
  current_up_price?: number
  current_down_price?: number
  live_crypto_price?: number
  win_odds?: number
  potential_profit?: number
  unrealized_pnl?: number
  // Trading decision metadata
  edge?: number
  true_prob?: number
  signal_strength?: number
  timing_bucket?: string
  reasoning?: string
}

interface CryptoPrices {
  BTC: number
  ETH: number
  SOL: number
  XRP: number
}

interface TimingBucket {
  bets: number
  wins: number
  losses: number
  win_rate: string
  roi: string
  pnl: string
}

interface TimingData {
  buckets: Record<string, TimingBucket>
  best_bucket: string
  best_roi: string
}

interface RiskData {
  daily_pnl: string
  daily_limit_used: string
  open_positions: number
  max_positions: number
  total_exposure: string
  max_exposure: string
  drawdown: string
  max_drawdown: string
  risk_level: string
}

interface DashboardData {
  stats: Stats
  open_positions: Position[]
  closed_positions: Position[]
  crypto_prices: CryptoPrices
  timing: TimingData
  risk: RiskData
  timestamp: string
}

interface LiveSignal {
  symbol: string
  direction: string
  confidence: number
  accuracy_estimate: number
  timestamp: string
  expiry_minutes: number
  rsi: number
  ema8_distance: number
  volatility_ratio: number
  hour_utc: number
  is_rsi_extreme: boolean
  is_ema_extended: boolean
  is_high_volatility: boolean
  is_good_hour: boolean
  reasoning: string
}

interface SignalsData {
  signals: Record<string, LiveSignal>
  timestamp: string
}

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

function StatCard({ label, value, subValue, color = 'white' }: { label: string; value: string; subValue?: string; color?: string }) {
  const colorClasses: Record<string, string> = {
    green: 'text-poly-green glow-green',
    red: 'text-poly-red glow-red',
    white: 'text-white',
    yellow: 'text-yellow-400',
  }
  
  return (
    <div className="bg-poly-card border border-poly-border rounded-lg p-4">
      <div className="text-gray-400 text-xs uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-2xl font-bold ${colorClasses[color]}`}>{value}</div>
      {subValue && <div className="text-gray-500 text-sm mt-1">{subValue}</div>}
    </div>
  )
}

function PriceCard({ asset, price }: { asset: string; price: number }) {
  const bgColors: Record<string, string> = {
    BTC: 'bg-orange-500/20 border-orange-500/50',
    ETH: 'bg-blue-500/20 border-blue-500/50',
    SOL: 'bg-purple-500/20 border-purple-500/50',
    XRP: 'bg-gray-500/20 border-gray-500/50',
  }
  
  return (
    <div className={`rounded-lg p-3 border ${bgColors[asset] || 'bg-gray-700'}`}>
      <div className="text-xs text-gray-400">{asset}</div>
      <div className="text-lg font-bold font-mono">${price.toLocaleString()}</div>
    </div>
  )
}

function AssetIcon({ asset }: { asset: string }) {
  const colors: Record<string, string> = {
    BTC: 'bg-orange-500',
    ETH: 'bg-blue-500',
    SOL: 'bg-purple-500',
    XRP: 'bg-gray-500',
  }

  return (
    <div className={`w-10 h-10 rounded-full ${colors[asset] || 'bg-gray-600'} flex items-center justify-center text-xs font-bold`}>
      {asset}
    </div>
  )
}

function TimingBucketCard({ name, bucket }: { name: string; bucket: TimingBucket }) {
  const roi = parseFloat(bucket.roi.replace('%', '').replace('+', ''))
  const roiColor = roi > 0 ? 'text-poly-green' : roi < 0 ? 'text-poly-red' : 'text-gray-400'
  
  return (
    <div className="bg-black/20 rounded-lg p-3">
      <div className="text-xs text-gray-400 mb-1">{name}</div>
      <div className="flex justify-between items-center">
        <div className="text-sm font-mono">{bucket.bets} bets</div>
        <div className={`text-sm font-mono font-bold ${roiColor}`}>{bucket.roi}</div>
      </div>
      <div className="text-xs text-gray-500 mt-1">{bucket.win_rate} WR</div>
    </div>
  )
}

function LiveSignalCard({ asset, signal }: { asset: string; signal: LiveSignal }) {
  const isUp = signal.direction === 'UP'
  const isDown = signal.direction === 'DOWN'
  const hasSignal = signal.direction !== 'NO_SIGNAL'
  
  const bgColors: Record<string, string> = {
    BTC: 'border-orange-500/50',
    ETH: 'border-blue-500/50',
    SOL: 'border-purple-500/50',
  }
  
  const expiryTime = new Date(new Date(signal.timestamp).getTime() + signal.expiry_minutes * 60000)
  const now = new Date()
  const timeLeft = Math.max(0, Math.floor((expiryTime.getTime() - now.getTime()) / 1000))
  const minsLeft = Math.floor(timeLeft / 60)
  const secsLeft = timeLeft % 60
  
  return (
    <div className={`bg-poly-card border-2 ${bgColors[asset] || 'border-poly-border'} rounded-xl p-4 ${hasSignal ? 'ring-2 ring-offset-2 ring-offset-poly-dark' : ''} ${isUp ? 'ring-poly-green' : isDown ? 'ring-poly-red' : ''}`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold ${asset === 'BTC' ? 'bg-orange-500' : asset === 'ETH' ? 'bg-blue-500' : 'bg-purple-500'}`}>
            {asset}
          </div>
          <div className="font-bold">{asset}/USDT</div>
        </div>
        {hasSignal && (
          <div className={`px-3 py-1 rounded-full text-sm font-bold ${isUp ? 'bg-poly-green/20 text-poly-green' : 'bg-poly-red/20 text-poly-red'}`}>
            {signal.direction}
          </div>
        )}
        {!hasSignal && (
          <div className="px-3 py-1 rounded-full text-sm font-bold bg-gray-700 text-gray-400">
            WAITING
          </div>
        )}
      </div>
      
      {hasSignal ? (
        <>
          <div className="grid grid-cols-2 gap-2 mb-3 text-sm">
            <div className="bg-black/20 rounded p-2">
              <div className="text-gray-500 text-xs">Confidence</div>
              <div className="font-mono font-bold">{(signal.confidence * 100).toFixed(0)}%</div>
            </div>
            <div className="bg-black/20 rounded p-2">
              <div className="text-gray-500 text-xs">Est. Accuracy</div>
              <div className="font-mono font-bold text-poly-green">{(signal.accuracy_estimate * 100).toFixed(0)}%</div>
            </div>
            <div className="bg-black/20 rounded p-2">
              <div className="text-gray-500 text-xs">RSI</div>
              <div className={`font-mono font-bold ${signal.is_rsi_extreme ? (signal.rsi < 50 ? 'text-poly-green' : 'text-poly-red') : ''}`}>
                {signal.rsi.toFixed(1)}
              </div>
            </div>
            <div className="bg-black/20 rounded p-2">
              <div className="text-gray-500 text-xs">EMA8 Dist</div>
              <div className={`font-mono font-bold ${signal.is_ema_extended ? (signal.ema8_distance < 0 ? 'text-poly-green' : 'text-poly-red') : ''}`}>
                {signal.ema8_distance > 0 ? '+' : ''}{signal.ema8_distance.toFixed(2)}%
              </div>
            </div>
          </div>
          
          <div className="flex items-center gap-1 flex-wrap mb-2">
            {signal.is_rsi_extreme && <span className="px-2 py-0.5 bg-blue-500/20 text-blue-400 rounded text-xs">RSI Extreme</span>}
            {signal.is_ema_extended && <span className="px-2 py-0.5 bg-purple-500/20 text-purple-400 rounded text-xs">EMA Extended</span>}
            {signal.is_high_volatility && <span className="px-2 py-0.5 bg-yellow-500/20 text-yellow-400 rounded text-xs">High Vol</span>}
            {signal.is_good_hour && <span className="px-2 py-0.5 bg-green-500/20 text-green-400 rounded text-xs">Good Hour</span>}
          </div>
          
          <div className="text-xs text-gray-400 mb-2">{signal.reasoning}</div>
          
          <div className="flex items-center justify-between text-xs">
            <span className="text-gray-500">Expires in:</span>
            <span className="font-mono text-yellow-400">{minsLeft}:{secsLeft.toString().padStart(2, '0')}</span>
          </div>
        </>
      ) : (
        <div className="text-center py-4 text-gray-500">
          <div className="text-xs mb-1">No extreme conditions</div>
          <div className="text-xs">RSI: {signal.rsi?.toFixed(1) || '-'} | EMA: {signal.ema8_distance?.toFixed(2) || '-'}%</div>
        </div>
      )}
    </div>
  )
}

function RiskIndicator({ risk }: { risk: RiskData }) {
  const dailyUsed = parseFloat(risk.daily_limit_used.replace('%', ''))
  const drawdown = parseFloat(risk.drawdown.replace('%', ''))
  
  const getColor = (pct: number) => {
    if (pct < 30) return 'bg-poly-green'
    if (pct < 60) return 'bg-yellow-500'
    return 'bg-poly-red'
  }

  return (
    <div className="bg-poly-card border border-poly-border rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-sm font-bold">Risk Status</div>
        <div className={`text-xs px-2 py-1 rounded ${risk.risk_level === 'CONSERVATIVE' ? 'bg-green-500/20 text-green-400' : risk.risk_level === 'MODERATE' ? 'bg-yellow-500/20 text-yellow-400' : 'bg-red-500/20 text-red-400'}`}>
          {risk.risk_level}
        </div>
      </div>
      
      <div className="space-y-3">
        <div>
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>Daily Loss</span>
            <span>{risk.daily_pnl} / ${risk.max_drawdown}</span>
          </div>
          <div className="h-2 bg-black/30 rounded-full overflow-hidden">
            <div className={`h-full ${getColor(dailyUsed)} transition-all`} style={{ width: `${Math.min(100, dailyUsed)}%` }} />
          </div>
        </div>
        
        <div>
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>Drawdown</span>
            <span>{risk.drawdown}</span>
          </div>
          <div className="h-2 bg-black/30 rounded-full overflow-hidden">
            <div className={`h-full ${getColor(drawdown * 4)} transition-all`} style={{ width: `${Math.min(100, drawdown * 4)}%` }} />
          </div>
        </div>
        
        <div className="flex justify-between text-xs pt-2 border-t border-poly-border/50">
          <span className="text-gray-400">Positions</span>
          <span className="font-mono">{risk.open_positions}/{risk.max_positions}</span>
        </div>
        <div className="flex justify-between text-xs">
          <span className="text-gray-400">Exposure</span>
          <span className="font-mono">{risk.total_exposure}</span>
        </div>
      </div>
    </div>
  )
}

function PositionCard({ position, isOpen }: { position: Position; isOpen: boolean }) {
  const formatTime = (iso: string) => {
    const date = new Date(iso)
    return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
  }

  const getTimeRemaining = (endTime: string) => {
    const end = new Date(endTime)
    const now = new Date()
    const diff = end.getTime() - now.getTime()
    if (diff <= 0) return 'Resolving...'
    const mins = Math.floor(diff / 60000)
    const secs = Math.floor((diff % 60000) / 1000)
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const winOdds = position.win_odds || (position.entry_price * 100)
  const oddsColor = winOdds >= 60 ? 'text-poly-green' : winOdds >= 40 ? 'text-yellow-400' : 'text-poly-red'

  return (
    <div className={`p-4 rounded-xl border ${
      isOpen 
        ? 'bg-poly-card/80 border-poly-border' 
        : position.status === 'won' 
          ? 'bg-poly-green/10 border-poly-green/30' 
          : 'bg-poly-red/10 border-poly-red/30'
    }`}>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <AssetIcon asset={position.asset} />
      <div>
            <div className="font-bold text-lg">
              {position.asset} <span className={position.side === 'Up' ? 'text-poly-green' : 'text-poly-red'}>{position.side}</span>
            </div>
            <div className="text-xs text-gray-500">
              {formatTime(position.start_time)} - {formatTime(position.end_time)}
            </div>
          </div>
        </div>
        {isOpen && (
          <div className="text-right">
            <div className="text-2xl font-mono text-yellow-400">
              {getTimeRemaining(position.end_time)}
            </div>
          </div>
        )}
      </div>
      
      {/* Details Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
        {/* Entry Price */}
        <div className="bg-black/20 rounded-lg p-2">
          <div className="text-gray-500 text-xs">Entry Price</div>
          <div className="font-mono font-bold">{(position.entry_price * 100).toFixed(1)}¢</div>
        </div>
        
        {/* Current Price */}
        {isOpen && position.current_price !== undefined && (
          <div className="bg-black/20 rounded-lg p-2">
            <div className="text-gray-500 text-xs">Current Price</div>
            <div className={`font-mono font-bold ${position.current_price > position.entry_price ? 'text-poly-green' : position.current_price < position.entry_price ? 'text-poly-red' : ''}`}>
              {(position.current_price * 100).toFixed(1)}¢
            </div>
          </div>
        )}
        
        {/* Win Odds */}
        {isOpen && (
          <div className="bg-black/20 rounded-lg p-2">
            <div className="text-gray-500 text-xs">Win Odds</div>
            <div className={`font-mono font-bold ${oddsColor}`}>
              {winOdds.toFixed(0)}%
            </div>
          </div>
        )}
        
        {/* Bet Amount */}
        <div className="bg-black/20 rounded-lg p-2">
          <div className="text-gray-500 text-xs">Bet Amount</div>
          <div className="font-mono font-bold">${position.amount_usd.toFixed(2)}</div>
        </div>
        
        {/* Potential Profit (if open) or P&L (if closed) */}
        {isOpen ? (
          <div className="bg-black/20 rounded-lg p-2">
            <div className="text-gray-500 text-xs">If Win</div>
            <div className="font-mono font-bold text-poly-green">
              +${(position.potential_profit || (position.shares - position.amount_usd)).toFixed(2)}
            </div>
          </div>
        ) : (
          <div className="bg-black/20 rounded-lg p-2">
            <div className="text-gray-500 text-xs">Result</div>
            <div className={`font-mono font-bold ${position.pnl && position.pnl >= 0 ? 'text-poly-green' : 'text-poly-red'}`}>
              {position.status === 'won' ? 'WON' : 'LOST'} {position.pnl && position.pnl >= 0 ? '+' : ''}${position.pnl?.toFixed(2)}
            </div>
          </div>
        )}
      </div>
      
      {/* Trading Decision Info */}
      {position.edge !== undefined && position.edge !== null && (
        <div className="mt-3 pt-3 border-t border-poly-border/50">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs mb-2">
            <div className="flex items-center gap-1">
              <span className="text-gray-500">Edge:</span>
              <span className="font-mono text-poly-green">{(position.edge * 100).toFixed(1)}%</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="text-gray-500">True Prob:</span>
              <span className="font-mono">{((position.true_prob || 0) * 100).toFixed(0)}%</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="text-gray-500">Signal:</span>
              <span className="font-mono">{((position.signal_strength || 0) * 100).toFixed(0)}%</span>
            </div>
            {position.timing_bucket && (
              <div className="flex items-center gap-1">
                <span className="text-gray-500">Bucket:</span>
                <span className="font-mono text-yellow-400">{position.timing_bucket}</span>
              </div>
            )}
          </div>
        </div>
      )}
      
      {/* Reasoning */}
      {position.reasoning && (
        <div className="mt-2 p-2 bg-black/30 rounded-lg">
          <div className="text-xs text-gray-400 mb-1">Why this bet:</div>
          <div className="text-xs text-gray-300 leading-relaxed">{position.reasoning}</div>
        </div>
      )}
      
      {/* Live Crypto Price */}
      {isOpen && position.live_crypto_price !== undefined && position.live_crypto_price > 0 && (
        <div className="mt-3 pt-3 border-t border-poly-border/50">
          <div className="flex items-center justify-between text-sm">
            <span className="text-gray-500">Live {position.asset} Price:</span>
            <span className="font-mono font-bold">${position.live_crypto_price.toLocaleString()}</span>
          </div>
        </div>
      )}
      
      {/* Market Prices */}
      {isOpen && position.current_up_price !== undefined && (
        <div className="mt-2 flex gap-4 text-xs">
          <div className="flex items-center gap-1">
            <span className="text-gray-500">Up:</span>
            <span className="font-mono text-poly-green">{(position.current_up_price * 100).toFixed(1)}¢</span>
          </div>
          <div className="flex items-center gap-1">
            <span className="text-gray-500">Down:</span>
            <span className="font-mono text-poly-red">{(position.current_down_price! * 100).toFixed(1)}¢</span>
          </div>
        </div>
      )}
    </div>
  )
}

type ViewMode = 'live' | 'paper'

function App() {
  const [viewMode, setViewMode] = useState<ViewMode>('paper')  // Default to paper trading
  const [data, setData] = useState<DashboardData | null>(null)
  const [signals, setSignals] = useState<SignalsData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null)
  
  // If paper trading mode, render the PaperTrading component
  if (viewMode === 'paper') {
    return (
      <div>
        {/* View Switcher */}
        <div className="fixed top-4 right-4 z-50 flex gap-2">
          <button
            onClick={() => setViewMode('live')}
            className="px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm hover:bg-gray-700 transition"
          >
            Live Trading
          </button>
          <button
            className="px-4 py-2 bg-purple-600 border border-purple-500 rounded-lg text-sm font-bold"
          >
            Paper Trading
          </button>
        </div>
        <PaperTrading />
      </div>
    )
  }

  const fetchData = async () => {
    try {
      const [dashResponse, signalsResponse] = await Promise.all([
        fetch(`${API_URL}/api/dashboard`),
        fetch(`${API_URL}/api/signals/live`)
      ])
      
      if (!dashResponse.ok) throw new Error('Failed to fetch dashboard data')
      const dashJson = await dashResponse.json()
      setData(dashJson)
      
      if (signalsResponse.ok) {
        const signalsJson = await signalsResponse.json()
        setSignals(signalsJson)
      }
      
      setError(null)
      setLastUpdate(new Date())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    // Fetch every 5 seconds for live signals - faster to catch opportunities
    const interval = setInterval(fetchData, 5000)
    return () => clearInterval(interval)
  }, [])

  if (loading) {
    return (
      <div className="min-h-screen bg-poly-dark flex items-center justify-center">
        <div className="text-2xl text-gray-400 animate-pulse">Loading...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-poly-dark flex flex-col items-center justify-center gap-4">
        <div className="text-2xl text-poly-red">Connection Error</div>
        <div className="text-gray-400">{error}</div>
        <div className="text-sm text-gray-500">API: {API_URL}</div>
        <button 
          onClick={() => { setLoading(true); fetchData(); }}
          className="mt-4 px-4 py-2 bg-poly-card border border-poly-border rounded-lg hover:bg-poly-border transition"
        >
          Retry
        </button>
      </div>
    )
  }

  const stats = data?.stats || { total_bets: 0, wins: 0, losses: 0, total_wagered: 0, total_pnl: 0, win_rate: 0 }
  const openPositions = data?.open_positions || []
  const closedPositions = data?.closed_positions || []
  const cryptoPrices = data?.crypto_prices || { BTC: 0, ETH: 0, SOL: 0, XRP: 0 }
  const timing = data?.timing
  const risk = data?.risk

  const roi = stats.total_wagered > 0 ? (stats.total_pnl / stats.total_wagered * 100) : 0

  return (
    <div className="min-h-screen bg-poly-dark p-4 md:p-6">
      <div className="max-w-7xl mx-auto">
        {/* View Switcher */}
        <div className="flex gap-2 mb-4">
          <button
            className="px-4 py-2 bg-blue-600 border border-blue-500 rounded-lg text-sm font-bold"
          >
            Live Trading
          </button>
          <button
            onClick={() => setViewMode('paper')}
            className="px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm hover:bg-gray-700 transition"
          >
            Paper Trading
          </button>
        </div>

        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-center justify-between mb-6 gap-4">
          <div>
            <h1 className="text-2xl md:text-3xl font-bold flex items-center gap-3">
              <span className="text-3xl md:text-4xl">&#8383;</span>
              Full-Stack Market Maker
            </h1>
            <p className="text-gray-500 mt-1">Polymarket 15-Min Crypto | Simulation Mode</p>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <div className="text-xs text-gray-500">Last update</div>
              <div className="text-sm text-gray-400">{lastUpdate?.toLocaleTimeString()}</div>
            </div>
            <div className="w-3 h-3 bg-poly-green rounded-full pulse-green"></div>
          </div>
        </div>

        {/* Live Crypto Prices */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
          <PriceCard asset="BTC" price={cryptoPrices.BTC} />
          <PriceCard asset="ETH" price={cryptoPrices.ETH} />
          <PriceCard asset="SOL" price={cryptoPrices.SOL} />
          <PriceCard asset="XRP" price={cryptoPrices.XRP} />
        </div>

        {/* Stats Grid */}
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-6">
          <StatCard 
            label="Total Bets" 
            value={stats.total_bets.toString()} 
          />
          <StatCard 
            label="Wins" 
            value={stats.wins.toString()} 
            color="green"
          />
          <StatCard 
            label="Losses" 
            value={stats.losses.toString()} 
            color="red"
          />
          <StatCard 
            label="Win Rate" 
            value={`${stats.win_rate.toFixed(1)}%`}
            subValue={`${stats.wins}/${stats.total_bets}`}
            color={stats.win_rate >= 50 ? 'green' : 'red'}
          />
          <StatCard 
            label="Total P&L" 
            value={`$${stats.total_pnl >= 0 ? '+' : ''}${stats.total_pnl.toFixed(2)}`}
            subValue={`Wagered: $${stats.total_wagered.toFixed(2)}`}
            color={stats.total_pnl >= 0 ? 'green' : 'red'}
          />
          <StatCard 
            label="ROI" 
            value={`${roi >= 0 ? '+' : ''}${roi.toFixed(1)}%`}
            subValue={timing?.best_bucket ? `Best: ${timing.best_bucket}` : undefined}
            color={roi >= 0 ? 'green' : 'red'}
          />
        </div>

        {/* Live Prediction Signals */}
        <div className="mb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-bold flex items-center gap-2">
              <span className="w-3 h-3 bg-blue-500 rounded-full animate-pulse"></span>
              Live Prediction Signals (7-min window)
            </h2>
            <div className="text-xs text-gray-500">
              Mean Reversion Strategy | 62% Expected Accuracy
            </div>
          </div>
          
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {signals?.signals ? (
              Object.entries(signals.signals).map(([asset, signal]) => (
                <LiveSignalCard key={asset} asset={asset} signal={signal as LiveSignal} />
              ))
            ) : (
              <div className="col-span-3 bg-poly-card border border-poly-border rounded-xl p-8 text-center text-gray-500">
                <div className="text-4xl mb-2">&#128161;</div>
                <div>Loading live signals...</div>
                <div className="text-sm">Analyzing BTC, ETH, SOL for mean-reversion opportunities</div>
              </div>
            )}
          </div>
        </div>

        {/* Timing & Risk Section */}
        <div className="grid md:grid-cols-2 gap-6 mb-6">
          {/* Timing Optimizer */}
          {timing && (
            <div className="bg-poly-card border border-poly-border rounded-lg p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="text-sm font-bold">Timing Optimizer (Thompson Sampling)</div>
                {timing.best_bucket && (
                  <div className="text-xs px-2 py-1 rounded bg-poly-green/20 text-poly-green">
                    Best: {timing.best_bucket}
                  </div>
                )}
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                {Object.entries(timing.buckets).map(([name, bucket]) => (
                  <TimingBucketCard key={name} name={name} bucket={bucket} />
                ))}
              </div>
            </div>
          )}

          {/* Risk Indicator */}
          {risk && <RiskIndicator risk={risk} />}
        </div>

        {/* Positions */}
        <div className="grid lg:grid-cols-2 gap-6">
          {/* Open Positions */}
          <div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-bold flex items-center gap-2">
                <span className="w-3 h-3 bg-yellow-400 rounded-full animate-pulse"></span>
                Open Positions
              </h2>
              <span className="text-sm text-gray-500">{openPositions.length} active</span>
            </div>
            
            {openPositions.length === 0 ? (
              <div className="bg-poly-card border border-poly-border rounded-xl p-8 text-center text-gray-500">
                <div className="text-4xl mb-2">&#128200;</div>
                <div>No open positions</div>
                <div className="text-sm">Waiting for opportunities...</div>
              </div>
            ) : (
              <div className="space-y-4">
                {openPositions.map(pos => (
                  <PositionCard key={pos.id} position={pos} isOpen={true} />
                ))}
              </div>
            )}
          </div>

          {/* Closed Positions */}
          <div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-bold">Recent Results</h2>
              <span className="text-sm text-gray-500">{closedPositions.length} shown</span>
            </div>
            
            {closedPositions.length === 0 ? (
              <div className="bg-poly-card border border-poly-border rounded-xl p-8 text-center text-gray-500">
                <div className="text-4xl mb-2">&#8987;</div>
                <div>No completed bets yet</div>
                <div className="text-sm">Results will appear here</div>
              </div>
            ) : (
              <div className="space-y-4">
                {closedPositions.map(pos => (
                  <PositionCard key={pos.id} position={pos} isOpen={false} />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="mt-8 text-center text-gray-600 text-sm">
          <div>&#127922; Simulation Mode - No real money at risk</div>
          <div className="mt-1">Full-Stack Trading System | Auto-refresh every 5s</div>
        </div>
      </div>
    </div>
  )
}

export default App
