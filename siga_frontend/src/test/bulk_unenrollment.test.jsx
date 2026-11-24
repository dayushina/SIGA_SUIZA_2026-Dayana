import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { apiClient } from '../core/api/client';
import { BulkUnenrollmentDialog } from '../modules/enrollment/BulkUnenrollmentDialog';

vi.mock('../core/api/client', () => ({ apiClient: { request: vi.fn() } }));

it('requiere periodo y confirmación, y solo envía las matrículas elegibles seleccionadas', async () => {
  const proto = HTMLDialogElement.prototype;
  const metodos = { showModal() { this.setAttribute('open', ''); }, close() { this.removeAttribute('open'); } };
  const agregados = Object.keys(metodos).filter(nombre => !proto[nombre]);
  for (const nombre of agregados) Object.defineProperty(proto, nombre, { value: metodos[nombre], configurable: true });
  const onApplied = vi.fn();
  apiClient.request
    .mockResolvedValueOnce([{ id: 1, codigo: '2027-1', estado: 'matricula_abierta' }])
    .mockResolvedValueOnce({ matriculas: [
      { id: 501, nombre: 'Alumno Elegible', dni: '00000501', programa_id: 2, ciclo: 1, bloqueo: null },
      { id: 502, nombre: 'Alumno Protegido', dni: '00000502', programa_id: 2, ciclo: 1, bloqueo: 'Tiene notas registradas.' },
    ] })
    .mockResolvedValueOnce({ anuladas: [501], omitidas: [] });
  const view = render(<BulkUnenrollmentDialog programas={[{ id: 2, nombre: 'Enfermería Técnica' }]}
    programaInicial="2" busquedaInicial="" onClose={vi.fn()} onApplied={onApplied} />);
  try {
    expect(screen.getByRole('button', { name: 'Revisar matrículas' })).toBeDisabled();
    await screen.findByRole('option', { name: /2027-1/ });
    fireEvent.change(screen.getByLabelText('Periodo académico'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Revisar matrículas' }));
    expect(await screen.findByRole('checkbox', { name: /Alumno Protegido/ })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Seleccionar todas las elegibles' }));
    fireEvent.change(screen.getByLabelText('Motivo de la desmatrícula'), { target: { value: 'Corrección de prueba' } });
    expect(screen.getByRole('button', { name: 'Desmatricular (1)' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Escribe DESMATRICULAR/), { target: { value: 'DESMATRICULAR' } });
    fireEvent.click(screen.getByRole('button', { name: 'Desmatricular (1)' }));
    expect(await screen.findByText('Matrículas anuladas: 1')).toBeInTheDocument();
    expect(apiClient.request).toHaveBeenNthCalledWith(3, '/api/mod-gestion-academica/matriculas/lote/anular', {
      method: 'POST', data: { periodo_id: 1, ids: [501], motivo: 'Corrección de prueba', confirmacion: 'DESMATRICULAR' },
    });
    expect(onApplied).toHaveBeenCalledOnce();
  } finally {
    view.unmount();
    for (const nombre of agregados) delete proto[nombre];
  }
});
