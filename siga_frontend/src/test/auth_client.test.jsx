import { describe, it, expect, vi, afterEach } from 'vitest';
import { apiClient } from '../core/api/client';

// Las credenciales nunca deben ir en la URL: quedarian en logs del gateway,
// proxies e historial del navegador.
describe('apiClient - credenciales en el body', () => {
  afterEach(() => vi.restoreAllMocks());

  const mockFetchOk = () =>
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ access_token: 't' }), { status: 200 })
    );

  it('login envia email y password como JSON y no en la query string', async () => {
    const fetchSpy = mockFetchOk();
    await apiClient.login('admin@siga.edu', 'p@ss&word');

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toMatch(/\/auth\/login$/);
    expect(url).not.toContain('password');
    expect(init.method).toBe('POST');
    expect(init.headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(init.body)).toEqual({ email: 'admin@siga.edu', password: 'p@ss&word' });
  });

  it('register envia los datos como JSON', async () => {
    const fetchSpy = mockFetchOk();
    await apiClient.register('nuevo@siga.edu', 'Clave12345', 'Nuevo Usuario');

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toMatch(/\/auth\/register$/);
    expect(JSON.parse(init.body)).toEqual({
      email: 'nuevo@siga.edu', password: 'Clave12345', full_name: 'Nuevo Usuario',
    });
  });
});
