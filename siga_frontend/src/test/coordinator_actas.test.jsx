import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react';
import React from 'react';
import { CoordinatorAcademic } from '../modules/academic/CoordinatorAcademic';
import { apiClient } from '../core/api/client';
import { UserManagement } from '../modules/admin/UserManagement';
import { ToastProvider } from '../core/ui/ToastContext';

// Mock del contexto de autenticación
let usuario;
vi.mock('../core/auth/useAuth', () => ({
  useAuth: () => ({
    user: usuario,
    openPasswordModal: vi.fn()
  })
}));

// Mock de apiClient
vi.mock('../core/api/client', () => ({
  apiClient: {
    request: vi.fn()
  }
}));

describe('Auditoría Frontend - Coordinación de Programa (Control de Actas)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    usuario = { id: 2, role: 'coordinador_programa', programa_id: 1 };
  });

  const mockInitialData = () => {
    apiClient.request.mockImplementation((endpoint, _options) => {
      if (endpoint === '/api/mod-programas-estudio/programas?implementados=true') {
        return Promise.resolve([{ id: 1, nombre: 'Desarrollo de Sistemas de Información' }]);
      }
      if (endpoint === '/api/mod-programas-estudio/periodos') {
        return Promise.resolve([{ id: 1, codigo: '2026-I', estado: 'ACTIVO' }]);
      }
      if (endpoint.includes('/api/mod-programas-estudio/programas/1/carga-lectiva')) {
        return Promise.resolve([]);
      }
      if (endpoint.includes('/api/mod-evaluacion/actas/periodo/1/programa/1')) {
        return Promise.resolve({
          periodo_id: 1,
          programa_id: 1,
          cargas: [
            {
              carga_id: 10,
              unidad: 'Taller de Programación Web',
              docente: 'Prof. Roberto Saboya',
              seccion: 'A',
              turno: 'Mañana',
              ciclo: 3,
              evaluados: 25,
              faltantes: 0,
              total_matriculas: 25,
              pendientes: 0,
              acta_cerrada: false,
              fecha_limite_notas: '2026-11-20',
              editable: true
            },
            {
              carga_id: 20,
              unidad: 'Base de Datos Avanzada',
              docente: 'Prof. Carlos Díaz',
              seccion: 'B',
              turno: 'Noche',
              ciclo: 4,
              evaluados: 18,
              faltantes: 2,
              total_matriculas: 20,
              pendientes: 0,
              acta_cerrada: true,
              fecha_limite_notas: '2026-11-15',
              editable: false
            }
          ],
          unidades_sin_carga: 1,
          unidades_sin_programa: 0,
          total_matriculas_pendientes: 0
        });
      }
      return Promise.resolve([]);
    });
  };

  it('1. Debe renderizar la pestaña Control de Actas con métricas y lista de cargas', async () => {
    mockInitialData();
    render(<CoordinatorAcademic />);

    // Esperar a que cargue y hacer clic en la pestaña Control de Actas
    const tabActas = await screen.findByTestId('tab-control-actas');
    fireEvent.click(tabActas);

    // Verificar panel visible
    await waitFor(() => {
      expect(screen.getByTestId('panel-control-actas')).toBeInTheDocument();
      expect(screen.getByText('Taller de Programación Web')).toBeInTheDocument();
      expect(screen.getByText('Base de Datos Avanzada')).toBeInTheDocument();
      expect(screen.getByText('Prof. Roberto Saboya')).toBeInTheDocument();
    });

    // Verificar insignias de estado
    expect(screen.getByTestId('estado-abierta-10')).toBeInTheDocument();
    expect(screen.getByTestId('estado-cerrada-20')).toBeInTheDocument();
  });

  it('2. Permite fijar fecha límite masiva enviando POST al backend', async () => {
    mockInitialData();
    render(<CoordinatorAcademic />);

    const tabActas = await screen.findByTestId('tab-control-actas');
    fireEvent.click(tabActas);

    // Abrir modal de fecha límite masiva
    const btnMasiva = await screen.findByTestId('btn-fecha-limite-masiva');
    fireEvent.click(btnMasiva);

    expect(screen.getByText('Fijar Fecha Límite Masiva')).toBeInTheDocument();

    // Llenar fecha y enviar
    const inputDate = screen.getByLabelText(/Nueva Fecha Límite/i);
    fireEvent.change(inputDate, { target: { value: '2026-12-01' } });

    const btnSubmit = screen.getByRole('button', { name: /Aplicar a Todas/i });
    fireEvent.click(btnSubmit);

    await waitFor(() => {
      expect(apiClient.request).toHaveBeenCalledWith(
        '/api/mod-evaluacion/actas/programa/1/periodo/1/fecha-limite',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ fecha_limite_notas: '2026-12-01' })
        })
      );
    });
  });

  it('3. Permite fijar fecha límite individual por carga con PUT', async () => {
    mockInitialData();
    render(<CoordinatorAcademic />);

    const tabActas = await screen.findByTestId('tab-control-actas');
    fireEvent.click(tabActas);

    // Abrir modal individual de carga 10
    const btnFecha = await screen.findByTestId('btn-fecha-10');
    fireEvent.click(btnFecha);

    expect(screen.getByText('Fecha Límite Individual')).toBeInTheDocument();

    const inputDate = screen.getByLabelText(/Fecha Límite de Notas/i);
    fireEvent.change(inputDate, { target: { value: '2026-11-28' } });

    const btnSubmit = screen.getByRole('button', { name: /Guardar Plazo/i });
    fireEvent.click(btnSubmit);

    await waitFor(() => {
      expect(apiClient.request).toHaveBeenCalledWith(
        '/api/mod-evaluacion/actas/carga/10/fecha-limite',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ fecha_limite_notas: '2026-11-28' })
        })
      );
    });
  });

  it('4. Permite cerrar formalmente un acta con modal preventivo de confirmación', async () => {
    mockInitialData();
    render(<CoordinatorAcademic />);

    const tabActas = await screen.findByTestId('tab-control-actas');
    fireEvent.click(tabActas);

    // Clic en cerrar acta 10 (con 0 faltantes)
    const btnCerrar = await screen.findByTestId('btn-cerrar-10');
    fireEvent.click(btnCerrar);

    expect(screen.getByText('¿Cerrar Acta Formalmente?')).toBeInTheDocument();

    const btnConfirmar = screen.getByRole('button', { name: /Confirmar Cierre de Acta/i });
    fireEvent.click(btnConfirmar);

    await waitFor(() => {
      expect(apiClient.request).toHaveBeenCalledWith(
        '/api/mod-evaluacion/actas/carga/10/cerrar',
        expect.objectContaining({
          method: 'POST'
        })
      );
    });
  });

  it('5. Permite reapertura excepcional de acta exigiendo motivo y fecha límite', async () => {
    mockInitialData();
    render(<CoordinatorAcademic />);

    const tabActas = await screen.findByTestId('tab-control-actas');
    fireEvent.click(tabActas);

    // Clic en reabrir acta 20 (que está cerrada)
    const btnReabrir = await screen.findByTestId('btn-reabrir-20');
    fireEvent.click(btnReabrir);

    expect(screen.getByText('Reapertura Excepcional de Acta')).toBeInTheDocument();

    // Completar motivo de >= 10 caracteres y nueva fecha
    const textarea = screen.getByPlaceholderText(/Rectificación de nota autorizada/i);
    fireEvent.change(textarea, { target: { value: 'Solicitud aprobada por Dirección para rectificación de examen' } });

    const btnSubmit = screen.getByRole('button', { name: /Reabrir Acta/i });
    fireEvent.click(btnSubmit);

    await waitFor(() => {
      expect(apiClient.request).toHaveBeenCalledWith(
        '/api/mod-evaluacion/actas/carga/20/reabrir',
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('Solicitud aprobada')
        })
      );
    });
  });
});

describe('Pertenencia académica y disponibilidad docente', () => {
  const programas = [{ id: 1, nombre: 'Contabilidad' }, { id: 2, nombre: 'Enfermería Técnica' }];
  const periodos = [{ id: 1, codigo: '2026-I', estado: 'en_curso' }, { id: 2, codigo: '2026-II', estado: 'planificacion' }];

  it('consulta la pareja seleccionada y descarta la respuesta de un periodo anterior', async () => {
    usuario = { id: 1, is_superuser: true };
    let resolverAnterior;
    apiClient.request.mockImplementation(path => {
      if (path.endsWith('/programas?implementados=true')) return Promise.resolve(programas);
      if (path.endsWith('/periodos')) return Promise.resolve(periodos);
      if (path.endsWith('/1/docentes-disponibles?periodo_id=1')) {
        return new Promise(resolve => { resolverAnterior = resolve; });
      }
      if (path.endsWith('/1/docentes-disponibles?periodo_id=2')) return Promise.resolve([{ id: 8, full_name: 'Docente Contabilidad' }]);
      if (path.endsWith('/2/docentes-disponibles?periodo_id=2')) return Promise.resolve([{ id: 9, full_name: 'Docente Enfermería' }]);
      return Promise.resolve([]);
    });
    render(<CoordinatorAcademic />);
    await waitFor(() => expect(resolverAnterior).toBeTypeOf('function'));
    fireEvent.change(screen.getByLabelText('Periodo lectivo'), { target: { value: '2' } });
    await screen.findByRole('option', { name: 'Docente Contabilidad' });
    fireEvent.change(screen.getByLabelText('Docente'), { target: { value: '8' } });
    fireEvent.change(screen.getByLabelText('Programa de estudios'), { target: { value: '2' } });
    await screen.findByRole('option', { name: 'Docente Enfermería' });
    await act(async () => resolverAnterior([{ id: 7, full_name: 'Docente del periodo anterior' }]));
    const selector = screen.getByLabelText('Docente');
    expect(selector).toHaveValue('');
    expect(within(selector).getAllByRole('option').map(op => op.textContent))
      .toEqual(['Seleccione Docente', 'Docente Enfermería']);
    expect(apiClient.request.mock.calls.some(([path]) => path.startsWith('/api/mod-usuarios/'))).toBe(false);
  });

  it.each(['coordinador_programa', 'docente', 'estudiante'])('exige un programa para %s', async rol => {
    apiClient.request.mockImplementation(path => {
      if (path.endsWith('/roles')) return Promise.resolve([{ id: 1, name: rol }]);
      if (path.endsWith('/programas?implementados=true')) return Promise.resolve(programas);
      if (path.endsWith('/periodos')) return Promise.resolve(periodos);
      return Promise.resolve([]);
    });
    render(<ToastProvider><UserManagement /></ToastProvider>);
    fireEvent.click(await screen.findByRole('button', { name: 'Nuevo Usuario' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: rol }));
    const selector = screen.getByRole('combobox', { name: /Programa de estudios implementado/ });
    expect(selector).toBeRequired();
    await within(selector).findByRole('option', { name: 'Contabilidad' });
    fireEvent.change(selector, { target: { value: '1' } });
    if (rol === 'docente') expect(await screen.findByRole('checkbox', { name: '2026-I' })).toBeVisible();
    else expect(screen.queryByText('Periodos lectivos habilitados para docencia')).not.toBeInTheDocument();
  });
});
