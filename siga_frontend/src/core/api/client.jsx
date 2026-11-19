// Sin barra final: los endpoints empiezan con "/" (evita "//api/...").
export const API_BASE = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '');
// WebSocket: mismo servidor que la API. En produccion (Vercel) el frontend y el
// backend estan en dominios distintos, asi que se deriva de VITE_API_URL
// (https -> wss). En desarrollo (API relativa) se usa el host actual + proxy de Vite.
export function resolveWsBase(apiBase = API_BASE, explicit = import.meta.env.VITE_WS_URL, loc = window.location) {
  if (explicit) return explicit.replace(/\/+$/, '');
  if (/^https?:\/\//i.test(apiBase || '')) {
    return apiBase.replace(/^http/i, 'ws').replace(/\/+$/, '');
  }
  return `${loc.protocol === 'https:' ? 'wss' : 'ws'}://${loc.host}`;
}
export const WS_BASE = resolveWsBase();

class SIGAApiClient {
  constructor() {
    this.baseURL = API_BASE;
    this.token = localStorage.getItem('token');
  }

  setToken(token) {
    this.token = token;
    localStorage.setItem('token', token);
  }

  clearToken() {
    this.token = null;
    localStorage.removeItem('token');
  }

  async request(endpoint, options = {}) {
    const url = `${this.baseURL}${endpoint}`;
    // `data` (objeto) se serializa como cuerpo JSON; fetch no conoce esa opcion
    // y sin esto la peticion viajaba vacia (PUT/POST fallaban con 422).
    const { timeoutMs, data, ...fetchOptions } = options;
    if (data !== undefined && fetchOptions.body === undefined) {
      fetchOptions.body = JSON.stringify(data);
    }
    const controller = timeoutMs ? new AbortController() : null;
    const timeoutId = controller
      ? setTimeout(() => controller.abort(), timeoutMs)
      : null;
    const config = {
      headers: {
        'Content-Type': 'application/json',
        ...fetchOptions.headers,
      },
      ...fetchOptions,
      ...(controller && !fetchOptions.signal ? { signal: controller.signal } : {}),
    };

    if (this.token) {
      config.headers.Authorization = `Bearer ${this.token}`;
    }

    try {
      const response = await fetch(url, config);

      if (response.status === 401) {
        if (endpoint.startsWith('/auth/login')) {
          let errorMsg = 'Credenciales inválidas';
          try {
            const errorData = await response.json();
            errorMsg = errorData.detail || errorMsg;
          } catch {
            // respuesta sin JSON: se mantiene el mensaje por defecto
          }
          throw new Error(errorMsg);
        }
        this.clearToken();
        window.location.reload();
        return;
      }

      if (!response.ok) {
        let errorMsg = `HTTP error! status: ${response.status}`;
        try {
          const errorData = await response.json();
          errorMsg = errorData.detail || errorMsg;
        } catch {
          // ignore
        }
        throw new Error(errorMsg);
      }

      return await response.json();
    } catch (error) {
      console.error('API Request failed:', error);
      throw error;
    } finally {
      if (timeoutId) clearTimeout(timeoutId);
    }
  }

  // Auth endpoints
  async login(email, password) {
    // Credenciales en el body: nunca en la URL (quedarian en logs/historial)
    return this.request('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password })
    });
  }

  async getCurrentUser() {
    return this.request('/auth/me', { timeoutMs: 10000 });
  }

  async register(email, password, fullName) {
    return this.request('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, password, full_name: fullName })
    });
  }

  // Module proxy calls
  async callModule(moduleName, endpoint, method = 'GET', data = null) {
    return this.request(`/api/${moduleName}/${endpoint}`, {
      method,
      body: data ? JSON.stringify(data) : null
    });
  }

  // Core system endpoints
  async getSystemStatus() {
    return this.request('/core/status');
  }

  async getModules() {
    return this.request('/core/modules');
  }
}

export const apiClient = new SIGAApiClient();

// WebSocket Client for real-time events
export class SIGAWSClient {
  constructor() {
    this.ws = null;
    this.listeners = {};
    this.connected = false;
    this.reconnectAttempts = 0;
    this.reconnectTimer = null;
    this.heartbeat = null;
    this.intentionalDisconnect = false;
    this._wake = () => { if (!this.connected && !this.intentionalDisconnect) this.reconnectNow(); };
    if (typeof window !== 'undefined') {
      window.addEventListener('online', this._wake);
      document.addEventListener('visibilitychange', () => { if (!document.hidden) this._wake(); });
    }
  }

  connect() {
    this.intentionalDisconnect = false;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      if (this.connected) setTimeout(() => this.trigger('connected'), 0);
      return;
    }

    let ws;
    try {
      ws = new WebSocket(`${WS_BASE}/ws`);
    } catch (error) {
      console.error('❌ WebSocket URL invalida:', error);
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      this.connected = true;
      this.reconnectAttempts = 0;
      this.trigger('connected');
      // Latido: los proxies (Railway, Vercel, redes moviles) cortan conexiones inactivas.
      clearInterval(this.heartbeat);
      this.heartbeat = setInterval(() => this.send('ping'), 25000);
    };

    ws.onmessage = (event) => {
      if (event.data === 'pong') return;
      this.trigger('message', event.data);
    };

    ws.onclose = () => {
      if (this.ws !== ws) return; // conexion antigua ya reemplazada
      clearInterval(this.heartbeat);
      this.ws = null;
      const wasConnected = this.connected;
      this.connected = false;
      if (wasConnected || this.reconnectAttempts === 0) this.trigger('disconnected');
      if (!this.intentionalDisconnect) this.scheduleReconnect();
    };

    ws.onerror = () => {
      // onclose se dispara a continuacion y gestiona la reconexion
    };
  }

  // Reintento indefinido con espera creciente (1s, 2s, 4s ... maximo 30s).
  scheduleReconnect() {
    clearTimeout(this.reconnectTimer);
    const delay = Math.min(30000, 1000 * 2 ** this.reconnectAttempts);
    this.reconnectAttempts += 1;
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  reconnectNow() {
    clearTimeout(this.reconnectTimer);
    this.reconnectAttempts = 0;
    this.connect();
  }

  disconnect() {
    this.intentionalDisconnect = true;
    clearTimeout(this.reconnectTimer);
    clearInterval(this.heartbeat);
    if (this.ws) {
      const ws = this.ws;
      this.ws = null;
      this.connected = false;
      ws.close();
      this.trigger('disconnected');
    }
  }

  send(data) {
    if (this.ws && this.connected) {
      this.ws.send(typeof data === 'string' ? data : JSON.stringify(data));
    }
  }

  on(event, callback) {
    if (!this.listeners[event]) {
      this.listeners[event] = [];
    }
    this.listeners[event].push(callback);
    return () => {
      this.listeners[event] = this.listeners[event].filter(cb => cb !== callback);
    };
  }

  trigger(event, data) {
    if (this.listeners[event]) {
      this.listeners[event].forEach(callback => callback(data));
    }
  }
}

export const wsClient = new SIGAWSClient();