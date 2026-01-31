import { useState, useEffect } from 'react'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

interface PaperPosition {
  id: string
  symbol: string
  direction: string
  trade_type: string
  entry_time: string
  entry_price: number
  position_size_usd: number
  polymarket_odds: number
  leverage: number
  confidence: number
  accuracy_estimate: number
  reasoning: string
  exit_time: string | null
  exit_price: number | null
  pnl: number | null
  won: boolean | null
  is_open: boolean
  expiry_time: string | null
}

interface LiveSignal {
  symbol: string
  direction: string
  confidence: number
  accuracy_estimate: number
  rsi: number
  ema8_distance: number
  reasoning: string
  timestamp: string
}

interface PaperStats {
  total_trades: number
  wins: number
  losses: number
  win_rate: string
  total_pnl: number
  daily_pnl: number
  bankroll: number
  starting_bankroll: number
  total_return: string
  polymarket_trades: number
  polymarket_pnl: number
  leverage_trades: number
  leverage_pnl: number
  consecutive_losses: number
}

interface PaperDashboardData {
  stats: PaperStats
  open_positions: {
    polymarket: PaperPosition[]
    leverage: PaperPosition[]
  }
  closed_positions: {
    polymarket: PaperPosition[]
    leverage: PaperPosition[]
  }
  signals: Record<string, LiveSignal>
  crypto_prices: Record<string, number>
  timestamp: string
}

function StatCard({ label, value, subValue, color = 'white' }: { 
  label: string
  value: string
  subValue?: string
  color?: string 
}) {
  const colorClasses: Record<string, string> = {
    green: 'text-green-400',
    red: 'text-red-400',
    white: 'text-white',
    yellow: 'text-yellow-400',
    blue: 'text-blue-400',
  }
  
  return (
    <div className="bg-gray-800/50 rounded-lg p-3">
      <div className="text-gray-400 text-xs uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-xl font-bold ${colorClasses[color]}`}>{value}</div>
      {subValue && <div className="text-gray-500 text-xs mt-1">{subValue}</div>}
    </div>
  )
}

function SignalBadge({ signal }: { signal: LiveSignal }) {
  const isUp = signal.direction === 'UP'
  const hasSignal = signal.direction !== 'NO_SIGNAL'
  
  if (!hasSignal) {
    return (
      <div className="flex items-center gap-2 text-gray-500 text-sm">
        <span className="w-2 h-2 bg-gray-500 rounded-full"></span>
        Waiting...
      </div>
    )
  }
  
  return (
    <div className={`flex items-center gap-2 text-sm font-bold ${isUp ? 'text-green-400' : 'text-red-400'}`}>
      <span className={`w-2 h-2 rounded-full animate-pulse ${isUp ? 'bg-green-400' : 'bg-red-400'}`}></span>
      {signal.direction} ({(signal.confidence * 100).toFixed(0)}%)
    </div>
  )
}

function PositionRow({ position, showType = false }: { position: PaperPosition; showType?: boolean }) {
  const isWin = position.won === true
  const isOpen = position.is_open
  
  const getTimeRemaining = () => {
    if (!position.expiry_time || !isOpen) return null
    const exp = new Date(position.expiry_time)
    const now = new Date()
    const diff = exp.getTime() - now.getTime()
    if (diff <= 0) return 'Expiring...'
    const mins = Math.floor(diff / 60000)
    const secs = Math.floor((diff % 60000) / 1000)
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }
  
  return (
    <div className={`p-3 rounded-lg border ${
      isOpen 
        ? 'bg-gray-800/50 border-gray-700' 
        : isWin 
          ? 'bg-green-900/20 border-green-700/50' 
          : 'bg-red-900/20 border-red-700/50'
    }`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className={`font-bold ${position.direction === 'UP' ? 'text-green-400' : 'text-red-400'}`}>
            {position.symbol.replace('USDT', '')} {position.direction}
          </span>
          {showType && (
            <span className={`text-xs px-2 py-0.5 rounded ${
              position.trade_type === 'polymarket' ? 'bg-purple-500/20 text-purple-400' : 'bg-orange-500/20 text-orange-400'
            }`}>
              {position.trade_type === 'polymarket' ? 'PM' : '2x'}
            </span>
          )}
        </div>
        {isOpen ? (
          <span className="text-yellow-400 font-mono text-sm">{getTimeRemaining()}</span>
        ) : (
          <span className={`font-bold ${isWin ? 'text-green-400' : 'text-red-400'}`}>
            {isWin ? 'WON' : 'LOST'} ${position.pnl?.toFixed(2)}
          </span>
        )}
      </div>
      
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div>
          <span className="text-gray-500">Size:</span>
          <span className="ml-1 font-mono">${position.position_size_usd.toFixed(2)}</span>
        </div>
        <div>
          <span className="text-gray-500">Entry:</span>
          <span className="ml-1 font-mono">${position.entry_price.toLocaleString()}</span>
        </div>
        <div>
          <span className="text-gray-500">Conf:</span>
          <span className="ml-1 font-mono">{(position.confidence * 100).toFixed(0)}%</span>
        </div>
      </div>
      
      {position.reasoning && (
        <div className="mt-2 text-xs text-gray-400 truncate">{position.reasoning}</div>
      )}
    </div>
  )
}

function TradingPanel({ 
  title, 
  icon,
  color,
  positions, 
  closedPositions,
  stats,
  signals,
  onTrade 
}: { 
  title: string
  icon: string
  color: string
  positions: PaperPosition[]
  closedPositions: PaperPosition[]
  stats: { trades: number; pnl: number }
  signals: Record<string, LiveSignal>
  onTrade: (symbol: string) => void
}) {
  const colorClasses: Record<string, string> = {
    purple: 'border-purple-500/50 bg-purple-500/5',
    orange: 'border-orange-500/50 bg-orange-500/5',
  }
  
  return (
    <div className={`border-2 rounded-xl p-4 ${colorClasses[color]}`}>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold flex items-center gap-2">
          <span className="text-2xl">{icon}</span>
          {title}
        </h2>
        <div className="text-right">
          <div className="text-sm text-gray-400">{stats.trades} trades</div>
          <div className={`font-bold ${stats.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            ${stats.pnl >= 0 ? '+' : ''}{stats.pnl.toFixed(2)}
          </div>
        </div>
      </div>
      
      {/* Signal buttons */}
      <div className="grid grid-cols-3 gap-2 mb-4">
        {Object.entries(signals).map(([symbol, signal]) => (
          <button
            key={symbol}
            onClick={() => onTrade(symbol + 'USDT')}
            disabled={signal.direction === 'NO_SIGNAL'}
            className={`p-2 rounded-lg border text-center transition ${
              signal.direction !== 'NO_SIGNAL'
                ? 'border-gray-600 bg-gray-800 hover:bg-gray-700 cursor-pointer'
                : 'border-gray-700/50 bg-gray-900/50 cursor-not-allowed opacity-50'
            }`}
          >
            <div className="font-bold text-sm">{symbol}</div>
            <SignalBadge signal={signal} />
          </button>
        ))}
      </div>
      
      {/* Open positions */}
      <div className="mb-4">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-bold text-gray-400 uppercase tracking-wider">
            Open Positions
          </h3>
          <span className="text-xs text-gray-500">{positions.length} active</span>
        </div>
        
        {positions.length === 0 ? (
          <div className="text-center py-4 text-gray-500 text-sm bg-gray-900/30 rounded-lg">
            No open positions
          </div>
        ) : (
          <div className="space-y-2 max-h-48 overflow-y-auto">
            {positions.map(pos => (
              <PositionRow key={pos.id} position={pos} />
            ))}
          </div>
        )}
      </div>
      
      {/* Closed positions */}
      <div>
        <h3 className="text-sm font-bold text-gray-400 uppercase tracking-wider mb-2">
          Recent Results
        </h3>
        
        {closedPositions.length === 0 ? (
          <div className="text-center py-4 text-gray-500 text-sm bg-gray-900/30 rounded-lg">
            No results yet
          </div>
        ) : (
          <div className="space-y-2 max-h-48 overflow-y-auto">
            {closedPositions.slice(0, 5).map(pos => (
              <PositionRow key={pos.id} position={pos} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function PaperTrading() {
  const [data, setData] = useState<PaperDashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null)

  const fetchData = async () => {
    try {
      const response = await fetch(`${API_URL}/api/paper/dashboard`)
      if (!response.ok) throw new Error('Failed to fetch data')
      const json = await response.json()
      setData(json)
      setError(null)
      setLastUpdate(new Date())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  const executeTrade = async (symbol: string, tradeType: string) => {
    try {
      const response = await fetch(
        `${API_URL}/api/paper/trade?symbol=${symbol}&trade_type=${tradeType}`,
        { method: 'POST' }
      )
      const result = await response.json()
      
      if (result.success) {
        // Refresh data
        await fetchData()
      } else {
        alert(result.message)
      }
    } catch (err) {
      alert('Trade failed: ' + (err instanceof Error ? err.message : 'Unknown error'))
    }
  }

  const resetTrading = async () => {
    if (!confirm('Reset all paper trading data?')) return
    
    try {
      await fetch(`${API_URL}/api/paper/reset`, { method: 'POST' })
      await fetchData()
    } catch (err) {
      alert('Reset failed')
    }
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 5000)
    return () => clearInterval(interval)
  }, [])

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center">
        <div className="text-2xl text-gray-400 animate-pulse">Loading Paper Trading...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-900 flex flex-col items-center justify-center gap-4">
        <div className="text-2xl text-red-400">Connection Error</div>
        <div className="text-gray-400">{error}</div>
        <button 
          onClick={() => { setLoading(true); fetchData(); }}
          className="mt-4 px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg hover:bg-gray-700 transition"
        >
          Retry
        </button>
      </div>
    )
  }

  const stats = data?.stats
  const signals = data?.signals || {}
  const cryptoPrices = data?.crypto_prices || {}

  return (
    <div className="min-h-screen bg-gray-900 text-white p-4">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-3">
              <span className="text-3xl">&#128200;</span>
              Paper Trading Dashboard
            </h1>
            <p className="text-gray-500 mt-1">Simulation Mode - No Real Money</p>
          </div>
          <div className="flex items-center gap-4">
            <button
              onClick={resetTrading}
              className="px-3 py-1 text-sm bg-red-900/30 border border-red-700/50 rounded hover:bg-red-900/50 transition"
            >
              Reset
            </button>
            <div className="text-right">
              <div className="text-xs text-gray-500">Last update</div>
              <div className="text-sm text-gray-400">{lastUpdate?.toLocaleTimeString()}</div>
            </div>
            <div className="w-3 h-3 bg-green-400 rounded-full animate-pulse"></div>
          </div>
        </div>

        {/* Crypto Prices */}
        <div className="flex gap-4 mb-6 overflow-x-auto pb-2">
          {Object.entries(cryptoPrices).map(([symbol, price]) => (
            <div key={symbol} className="flex-shrink-0 bg-gray-800/50 rounded-lg px-4 py-2">
              <span className="text-gray-400 text-sm">{symbol}</span>
              <span className="ml-2 font-mono font-bold">${price?.toLocaleString() || '-'}</span>
            </div>
          ))}
        </div>

        {/* Overall Stats */}
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-6">
          <StatCard 
            label="Bankroll" 
            value={`$${stats?.bankroll?.toFixed(2) || '0'}`}
            subValue={`Started: $${stats?.starting_bankroll || 1000}`}
            color="blue"
          />
          <StatCard 
            label="Total P&L" 
            value={`$${stats?.total_pnl?.toFixed(2) || '0'}`}
            subValue={stats?.total_return || '0%'}
            color={(stats?.total_pnl || 0) >= 0 ? 'green' : 'red'}
          />
          <StatCard 
            label="Daily P&L" 
            value={`$${stats?.daily_pnl?.toFixed(2) || '0'}`}
            color={(stats?.daily_pnl || 0) >= 0 ? 'green' : 'red'}
          />
          <StatCard 
            label="Win Rate" 
            value={stats?.win_rate || '0%'}
            subValue={`${stats?.wins || 0}W / ${stats?.losses || 0}L`}
          />
          <StatCard 
            label="Total Trades" 
            value={String(stats?.total_trades || 0)}
          />
          <StatCard 
            label="Loss Streak" 
            value={String(stats?.consecutive_losses || 0)}
            color={(stats?.consecutive_losses || 0) >= 3 ? 'red' : 'white'}
          />
        </div>

        {/* Split View: Polymarket vs Leverage */}
        <div className="grid lg:grid-cols-2 gap-6">
          {/* Polymarket Panel */}
          <TradingPanel
            title="Polymarket Binary"
            icon="&#127922;"
            color="purple"
            positions={data?.open_positions?.polymarket || []}
            closedPositions={data?.closed_positions?.polymarket || []}
            stats={{
              trades: stats?.polymarket_trades || 0,
              pnl: stats?.polymarket_pnl || 0,
            }}
            signals={signals}
            onTrade={(symbol) => executeTrade(symbol, 'polymarket')}
          />
          
          {/* Leverage Panel */}
          <TradingPanel
            title="2x Leverage Trading"
            icon="&#128640;"
            color="orange"
            positions={data?.open_positions?.leverage || []}
            closedPositions={data?.closed_positions?.leverage || []}
            stats={{
              trades: stats?.leverage_trades || 0,
              pnl: stats?.leverage_pnl || 0,
            }}
            signals={signals}
            onTrade={(symbol) => executeTrade(symbol, 'leverage')}
          />
        </div>

        {/* Strategy Info */}
        <div className="mt-6 bg-gray-800/30 rounded-xl p-4 border border-gray-700/50">
          <h3 className="font-bold mb-2">Mean Reversion Strategy</h3>
          <div className="grid md:grid-cols-4 gap-4 text-sm text-gray-400">
            <div>
              <span className="text-gray-500">Expected Accuracy:</span>
              <span className="ml-2 text-green-400 font-bold">62%</span>
            </div>
            <div>
              <span className="text-gray-500">Window:</span>
              <span className="ml-2">7 minutes</span>
            </div>
            <div>
              <span className="text-gray-500">Position Sizing:</span>
              <span className="ml-2">Kelly Criterion (25%)</span>
            </div>
            <div>
              <span className="text-gray-500">Risk Limits:</span>
              <span className="ml-2">$100/day max loss</span>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="mt-8 text-center text-gray-600 text-sm">
          <div>Paper Trading Mode - Track performance without real money</div>
          <div className="mt-1">Auto-refresh every 5s | Positions expire after 7 minutes</div>
        </div>
      </div>
    </div>
  )
}
