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

describe('Editar periodos del calendario', () => {
  it('ofrece la edición en los tres estados admitidos a Secretaría Académica', async () => {
    user = { roles: ['secretaria_academica'] };
    const estados = ['planificacion', 'matricula_abierta', 'en_curso', 'cerrado', 'historico', 'activo'];
    apiClient.callModule.mockResolvedValue(estados.map((estado, i) => ({
      ...periodo, id: 20 + i, codigo: `PER-${i}`, estado,
    })));
    montar();

    await screen.findByRole('button', { name: 'Editar periodo PER-0' });
    expect(screen.getAllByRole('button', { name: /Editar periodo/ }).map(b => b.getAttribute('aria-label')))
      .toEqual(['Editar periodo PER-0', 'Editar periodo PER-1', 'Editar periodo PER-2']);
  });

  it('oculta la edición a un docente', async () => {
    user = { roles: ['docente'] };
    montar();
    await screen.findByText('2029-I');
    expect(screen.queryByRole('button', { name: /Editar periodo/ })).toBeNull();
  });

  it('precarga las fechas y conserva la hora de una fecha que no se cambió', async () => {
    const onUpdate = vi.fn();
    const existente = {
      ...periodo, fecha_inicio: '2029-01-01T09:30:00', fecha_fin_matricula_regular: '2028-12-20T23:59:59',
      updated_at: '2028-10-11T17:00:42.211040',
    };
    apiClient.callModule.mockResolvedValueOnce([existente])
      .mockResolvedValueOnce({ ...existente, codigo: '2029-I-A', fecha_fin: '2029-07-05T00:00:00' });
    montar(onUpdate);

    fireEvent.change(await screen.findByLabelText('Código del Periodo'), { target: { value: 'Borrador' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Editar periodo 2029-I' }));
    expect(screen.getByLabelText('Código del Periodo')).toHaveValue('2029-I');
    expect(screen.getByLabelText('Fecha Inicio')).toHaveValue('2029-01-01');
    expect(screen.getByLabelText('Cierre Matrícula Regular')).toHaveValue('2028-12-20');
    expect(screen.queryByLabelText('Estado Inicial')).toBeNull();
    fireEvent.change(screen.getByLabelText('Código del Periodo'), { target: { value: '2029-I-A' } });
    fireEvent.change(screen.getByLabelText('Fecha Fin'), { target: { value: '2029-07-05' } });
    fireEvent.click(screen.getByRole('button', { name: 'Guardar cambios' }));

    await screen.findByText('2029-I-A');
    expect(apiClient.callModule).toHaveBeenCalledWith('mod-programas-estudio', 'periodos/20', 'PUT', {
      codigo: '2029-I-A', fecha_inicio: '2029-01-01T09:30:00', fecha_fin: '2029-07-05',
      fecha_fin_matricula_regular: '2028-12-20T23:59:59', fecha_fin_matricula_extemporanea: null,
      updated_at: '2028-10-11T17:00:42.211040',
    });
    expect(screen.getByLabelText('Código del Periodo')).toHaveValue('Borrador');
    expect(onUpdate).toHaveBeenCalledOnce();
  });

  it('refresca el estado si otra sesión cierra el periodo mientras se edita', async () => {
    apiClient.callModule.mockResolvedValueOnce([periodo])
      .mockRejectedValueOnce(new Error('El periodo fue cerrado.'))
      .mockResolvedValueOnce([{ ...periodo, estado: 'historico' }]);
    montar();

    fireEvent.click(await screen.findByRole('button', { name: 'Editar periodo 2029-I' }));
    fireEvent.click(screen.getByRole('button', { name: 'Guardar cambios' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('El periodo fue cerrado.');
    await screen.findByText('Histórico', { selector: 'span' });
    expect(screen.getByRole('button', { name: 'Guardar cambios' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }));
    expect(screen.queryByRole('button', { name: /Editar periodo/ })).toBeNull();
  });
});
