import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { PeriodManager } from '../modules/academic/PeriodManager';
import { ConfirmProvider } from '../core/ui/ConfirmModal';
import { ToastProvider } from '../core/ui/ToastContext';
import { apiClient } from '../core/api/client';

let user;
vi.mock('../core/auth/useAuth', () => ({ useAuth: () => ({ user }) }));
vi.mock('../core/api/client', () => ({ apiClient: { callModule: vi.fn() } }));

const periodo = {
  id: 20, codigo: '2029-I', estado: 'planificacion', fecha_inicio: '2029-01-01', fecha_fin: '2029-06-30',
};

function montar(onUpdate = vi.fn()) {
  render(
    <ConfirmProvider>
      <ToastProvider>
        <PeriodManager onClose={vi.fn()} onUpdate={onUpdate} />
      </ToastProvider>
    </ConfirmProvider>,
  );
}

beforeEach(() => {
  user = { roles: ['superadmin'] };
  apiClient.callModule.mockReset().mockResolvedValue([periodo]);
});

describe('Eliminar periodos del calendario', () => {
  it('pide confirmación y solo actualiza el calendario después de eliminar', async () => {
    const actualizar = vi.fn();
    apiClient.callModule.mockResolvedValueOnce([periodo])
      .mockResolvedValueOnce({ id: 20, message: 'Periodo 2029-I eliminado.' });
    montar(actualizar);

    fireEvent.click(await screen.findByRole('button', { name: 'Eliminar periodo 2029-I' }));
    await screen.findByText('Eliminar el periodo 2029-I');
    expect(apiClient.callModule).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId('confirm-modal-cancel'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Eliminar periodo 2029-I' })).toBeEnabled());

    fireEvent.click(screen.getByRole('button', { name: 'Eliminar periodo 2029-I' }));
    fireEvent.click(await screen.findByTestId('confirm-modal-ok'));
    expect(await screen.findByText('No hay periodos registrados aún.')).toBeVisible();
    expect(apiClient.callModule).toHaveBeenCalledWith('mod-programas-estudio', 'periodos/20', 'DELETE');
    expect(actualizar).toHaveBeenCalledTimes(1);
  });

  it('conserva la fila cuando el servidor informa dependencias', async () => {
    apiClient.callModule.mockResolvedValueOnce([periodo])
      .mockRejectedValueOnce(new Error('El periodo 2029-I tiene registros vinculados: matrículas (3).'));
    montar();
    fireEvent.click(await screen.findByRole('button', { name: 'Eliminar periodo 2029-I' }));
    fireEvent.click(await screen.findByTestId('confirm-modal-ok'));
    expect(await screen.findByRole('alert')).toHaveTextContent('matrículas (3)');
    expect(screen.getByRole('button', { name: 'Eliminar periodo 2029-I' })).toBeVisible();
  });

  it('oculta la acción a otros roles', async () => {
    user = { roles: ['secretaria_academica'] };
    montar();
    await screen.findByText('2029-I');
    expect(screen.queryByRole('button', { name: /Eliminar periodo/ })).toBeNull();
  });
});
