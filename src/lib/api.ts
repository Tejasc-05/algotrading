/** Centralized API client for AlgoFlow backend.
 *  All calls include auth header automatically.
 */
const API_BASE = import.meta.env.VITE_API_BASE || '/api';

class ApiError extends Error {
  status: number;
  code: string;

  constructor(
    message: string,
    status: number,
    code: string
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

function getAuthHeader(): Record<string, string> {
  const token = localStorage.getItem('access_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader(),
      ...options.headers,
    },
  });

  if (!response.ok) {
    let message = 'Request failed';
    let code = 'REQUEST_FAILED';
    try {
      const err = await response.json();
      message = err.error?.message || message;
      code = err.error?.code || code;
    } catch {
      // ignore parse errors
    }
    throw new ApiError(message, response.status, code);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
}

export const api = {
  // Auth
  auth: {
    register: (email: string, password: string) =>
      request<{ access_token: string; user: any }>('/auth/register', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      }),
    login: (email: string, password: string) =>
      request<{ access_token: string; user: any }>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      }),
    me: () => request<any>('/auth/me'),
  },

  // Strategies
  strategies: {
    list: () => request<any[]>('/strategies'),
    get: (id: string) => request<any>(`/strategies/${id}`),
    create: (payload: { name: string; description?: string; graph: any }) =>
      request<any>('/strategies', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    update: (id: string, payload: { name?: string; description?: string; graph: any }) =>
      request<any>(`/strategies/${id}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      }),
    delete: (id: string) => request<void>(`/strategies/${id}`, { method: 'DELETE' }),
    validate: (id: string, graph?: any) =>
      request<any>(`/strategies/${id}/validate`, {
        method: 'POST',
        body: JSON.stringify({ graph }),
      }),
    execute: (id: string) =>
      request<any>(`/strategies/${id}/execute`, { method: 'POST' }),
  },

  // Market Data
  market: {
    ticker: (symbol: string) => request<any>(`/market/ticker?symbol=${encodeURIComponent(symbol)}`),
    ohlcv: (symbol: string, timeframe = '1h', limit = 100) =>
      request<any>(`/market/ohlcv?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}`),
  },

  // Sentiment
  sentiment: {
    get: (symbol: string, provider = 'newsapi') =>
      request<any>(`/sentiment/${encodeURIComponent(symbol)}?provider=${provider}`),
  },

  // Backtesting
  backtest: {
    run: (payload: {
      strategy_id: string;
      strategy_version?: number;
      symbol: string;
      timeframe: string;
      start_date: string;
      end_date: string;
      starting_capital: number;
      fees_pct: number;
      slippage_pct: number;
      engine: 'vectorbt' | 'backtrader';
    }) => request<any>('/backtest', { method: 'POST', body: JSON.stringify(payload) }),
    get: (id: string) => request<any>(`/backtest/${id}`),
  },

  // Paper Trading
  paperTrading: {
    start: (payload: { strategy_id: string; symbol: string; timeframe?: string; starting_balance?: number }) =>
      request<any>('/paper-trading/start', { method: 'POST', body: JSON.stringify(payload) }),
    getAccount: () => request<any>('/paper-trading/account'),
    getTrades: () => request<any[]>('/paper-trading/trades'),
    createOrder: (payload: { paper_account_id: string; symbol: string; side: string; order_type: string; quantity: number; price?: number }) =>
      request<any>('/paper-trading/orders', { method: 'POST', body: JSON.stringify(payload) }),
    simulateFill: (order_id: string, current_price: number) =>
      request<any>('/paper-trading/simulate-fill', { method: 'POST', body: JSON.stringify({ order_id, current_price }) }),
  },

  // Exchanges
  exchanges: {
    connect: (payload: { exchange_name: string; label?: string; api_key: string; api_secret: string; passphrase?: string; is_testnet?: boolean }) =>
      request<any>('/exchanges/connect', { method: 'POST', body: JSON.stringify(payload) }),
    list: () => request<any[]>('/exchanges'),
    disconnect: (id: string) => request<void>(`/exchanges/${id}`, { method: 'DELETE' }),
    test: (id: string) => request<any>(`/exchanges/${id}/test`, { method: 'POST' }),
  },

  // Portfolio
  portfolio: {
    get: () => request<any>('/portfolio'),
    performance: () => request<any>('/portfolio/performance'),
  },

  // Errors
  ApiError,
};

export type { ApiError };