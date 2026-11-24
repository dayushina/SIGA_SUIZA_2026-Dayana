import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { apiClient } from '../core/api/client';
import { UserManagement } from '../modules/admin/UserManagement';

vi.mock('../core/api/client', () => ({ apiClient: { request: vi.fn() } }));
vi.mock('../core/ui/ToastContext', () => ({ useToast: () => ({ error: vi.fn() }) }));

it('exige un programa implementado al crear una cuenta de Secretaría de Programa', async () => {
  apiClient.request.mockImplementation(async (url, options) => {
    if (url.endsWith('/roles')) return [{ id: 8, name: 'secretaria_programa' }];
    if (url.endsWith('/programas?implementados=true')) return [{ id: 2, nombre: 'Enfermería Técnica' }];
    return options?.method === 'POST' ? {} : [];
  });
  render(<UserManagement />);
  fireEvent.click(await screen.findByRole('button', { name: 'Nuevo Usuario' }));
  expect(screen.queryByLabelText(/Programa de estudios implementado/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox', { name: 'secretaria_programa' }));
  const programa = screen.getByLabelText(/Programa de estudios implementado/);
  expect(programa).toBeRequired();
  expect(await screen.findByRole('option', { name: 'Enfermería Técnica' })).toBeInTheDocument();

  fireEvent.change(screen.getByPlaceholderText(/Ej. Juan/), { target: { value: 'Secretaría de prueba' } });
  fireEvent.change(screen.getByPlaceholderText('ejemplo@siga.edu'), { target: { value: 'secretaria@siga.edu' } });
  fireEvent.change(screen.getByPlaceholderText(/6 caracteres/), { target: { value: 'Clave-Prueba-2026' } });
  fireEvent.change(programa, { target: { value: '2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Crear Usuario' }));

  await waitFor(() => {
    const llamada = apiClient.request.mock.calls.find(([, options]) => options?.method === 'POST');
    expect(JSON.parse(llamada[1].body)).toMatchObject({ role_ids: [8], programa_estudio_id: 2 });
  });
});
