import { describe, it, expect } from 'vitest';
import { resolveWsBase } from '../core/api/client';
import { tieneRol, puedeAnularPagos, puedeDesmatricular, puedeEliminarEstudiantes } from '../core/auth/roles';

const vercel = { protocol: 'https:', host: 'siga-suiza.vercel.app' };

describe('resolveWsBase (indicador En línea / Sin conexión)', () => {
  it('en producción usa el dominio del backend, no el del frontend', () => {
    expect(resolveWsBase('https://backend-production-f112b04f.up.railway.app', undefined, vercel))
      .toBe('wss://backend-production-f112b04f.up.railway.app');
  });

  it('tolera la barra final en VITE_API_URL y http -> ws', () => {
    expect(resolveWsBase('http://localhost:8000/', undefined, vercel)).toBe('ws://localhost:8000');
  });

  it('VITE_WS_URL explícita tiene prioridad', () => {
    expect(resolveWsBase('https://api.x.com', 'wss://ws.x.com/', vercel)).toBe('wss://ws.x.com');
  });

  it('en desarrollo (API relativa) usa el host actual y el proxy de Vite', () => {
    expect(resolveWsBase('', undefined, { protocol: 'http:', host: 'localhost:5173' })).toBe('ws://localhost:5173');
  });
});

describe('permisos de acciones destructivas', () => {
  const u = (roles, extra = {}) => ({ roles, ...extra });

  it('superadmin puede todo (por rol o por flag)', () => {
    for (const user of [u(['superadmin']), u([], { is_superuser: true }), u([{ name: 'superadmin' }])]) {
      expect(puedeAnularPagos(user) && puedeDesmatricular(user) && puedeEliminarEstudiantes(user)).toBe(true);
    }
  });

  it('cada rol solo su acción', () => {
    expect(puedeAnularPagos(u(['caja_tesoreria']))).toBe(true);
    expect(puedeDesmatricular(u(['caja_tesoreria']))).toBe(false);
    expect(puedeDesmatricular(u(['secretaria_academica']))).toBe(true);
    expect(puedeEliminarEstudiantes(u(['secretaria_academica']))).toBe(false);
    expect(tieneRol(null, 'docente')).toBe(false);
  });
});
