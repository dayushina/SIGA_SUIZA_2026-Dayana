import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ToastProvider } from '../core/ui/ToastContext';
import { ConfirmProvider } from '../core/ui/ConfirmModal';
import { StudentDetail } from '../modules/students/StudentDetail';

let mockUser;
vi.mock('../core/auth/useAuth', () => ({ useAuth: () => ({ user: mockUser }) }));

const estudiante = { id: 7, nombres: 'Ana', apellidos: 'Prueba', dni: '12345678', codigo_estudiante: '2026-000007', programa_id: 1 };
const matriculas = [
  { id: 70, estudiante_id: 7, programa_id: 1, periodo_id: 1, tipo_ingreso: 'Ordinario', estado_matricula: 'confirmada', consolidada: false, detalles: [] },
  { id: 69, estudiante_id: 7, programa_id: 1, periodo_id: 1, tipo_ingreso: 'Ordinario', estado_matricula: 'anulada', anulada_motivo: 'error', detalles: [] },
];

const respuesta = (body) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });

function renderFicha() {
  return render(
    <ToastProvider>
      <ConfirmProvider>
        <StudentDetail student={estudiante} onClose={() => {}} />
      </ConfirmProvider>
    </ToastProvider>,
  );
}

describe('Ficha del estudiante: acciones de baja visibles', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn((url) => respuesta(
      url.includes('/matriculas/estudiante/') ? matriculas : url.includes('/estudiantes/7') ? estudiante : [],
    ));
  });

  it('superadmin ve "Eliminar estudiante" y "Desmatricular" solo en la matrícula vigente', async () => {
    mockUser = { roles: ['superadmin'], is_superuser: true };
    renderFicha();
    expect(await screen.findByRole('button', { name: /Eliminar estudiante/i })).toBeTruthy();
    screen.getByRole('button', { name: /Matrículas/i }).click();
    await waitFor(() => expect(screen.getAllByRole('button', { name: /Desmatricular/i })).toHaveLength(1));
  });

  it('un rol sin permiso no ve las acciones', async () => {
    mockUser = { roles: ['docente'], is_superuser: false };
    renderFicha();
    await screen.findByText(/Cerrar Ficha/i);
    expect(screen.queryByRole('button', { name: /Eliminar estudiante/i })).toBeNull();
    screen.getByRole('button', { name: /Matrículas/i }).click();
    await waitFor(() => expect(screen.queryByRole('button', { name: /Desmatricular/i })).toBeNull());
  });
});
