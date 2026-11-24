import { fireEvent, render, screen } from '@testing-library/react';
import { afterAll, beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { apiClient } from '../core/api/client';
import { BulkDeleteStudentsDialog } from '../modules/students/BulkDeleteStudentsDialog';

vi.mock('../core/api/client', () => ({ apiClient: { request: vi.fn() } }));

const students = [
  { id: 1, nombres: 'Ana', apellidos: 'Prueba', codigo_estudiante: 'T-1' },
  { id: 2, nombres: 'Luis', apellidos: 'Ejemplo', codigo_estudiante: 'T-2' },
];

const dialogMethods = {
  showModal() { this.setAttribute('open', ''); },
  close() { this.removeAttribute('open'); },
};
const addedMethods = Object.keys(dialogMethods).filter(name => !HTMLDialogElement.prototype[name]);

beforeAll(() => {
  for (const name of addedMethods) {
    Object.defineProperty(HTMLDialogElement.prototype, name, { value: dialogMethods[name], configurable: true });
  }
});

afterAll(() => {
  for (const name of addedMethods) delete HTMLDialogElement.prototype[name];
});

beforeEach(() => apiClient.request.mockReset());

function renderDialog(onDeleted = vi.fn()) {
  render(<BulkDeleteStudentsDialog students={students} pageStudents={students.slice(0, 1)}
    scopeLabel="Incluye todo el padrón." onClose={vi.fn()} onDeleted={onDeleted} />);
}

it('revisa todos los resultados y únicamente envía para eliminación los elegibles confirmados', async () => {
  const onDeleted = vi.fn();
  apiClient.request
    .mockResolvedValueOnce({ elegibles: [1], omitidos: [{ id: 2, motivos: ['1 registro de notas'] }] })
    .mockResolvedValueOnce({ eliminados: [1], omitidos: [] });
  renderDialog(onDeleted);

  expect(screen.getByRole('button', { name: 'Revisar selección' })).toBeDisabled();
  fireEvent.click(screen.getByRole('radio', { name: /Todos los resultados/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Revisar selección' }));
  expect(await screen.findByText('Revisión del lote')).toBeInTheDocument();
  expect(screen.getByText('Ver los motivos de omisión (1)')).toBeInTheDocument();
  expect(apiClient.request.mock.calls[0]).toEqual([
    '/api/mod-gestion-academica/estudiantes/lote/vista-previa',
    { method: 'POST', data: { ids: [1, 2] } },
  ]);

  const deleteButton = screen.getByRole('button', { name: 'Eliminar (1)' });
  fireEvent.change(screen.getByLabelText(/Motivo de la eliminación/), { target: { value: 'Duplicado' } });
  expect(deleteButton).toBeDisabled();
  fireEvent.change(screen.getByLabelText(/para confirmar/), { target: { value: 'ELIMINAR' } });
  fireEvent.click(deleteButton);

  expect(await screen.findByText('Resultado de la eliminación')).toBeInTheDocument();
  expect(apiClient.request.mock.calls[1]).toEqual([
    '/api/mod-gestion-academica/estudiantes/lote/eliminar',
    { method: 'POST', data: { ids: [1], motivo: 'Duplicado', confirmacion: 'ELIMINAR' } },
  ]);
  expect(onDeleted).toHaveBeenCalledWith([1]);
});

it('limita la página actual a sus filas e impide confirmar un lote sin elegibles', async () => {
  apiClient.request.mockResolvedValueOnce({ elegibles: [], omitidos: [{ id: 1, motivos: ['1 matrícula vigente'] }] });
  renderDialog();
  fireEvent.click(screen.getByRole('radio', { name: /Página actual/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Revisar selección' }));
  expect(await screen.findByText('Revisión del lote')).toBeInTheDocument();
  expect(apiClient.request.mock.calls[0][1].data).toEqual({ ids: [1] });
  expect(screen.queryByRole('button', { name: /^Eliminar/ })).not.toBeInTheDocument();
});
