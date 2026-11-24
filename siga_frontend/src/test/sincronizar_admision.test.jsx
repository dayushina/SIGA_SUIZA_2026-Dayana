// Registro de Estudiantes -> aviso "Admision publico N ingresantes" -> "Registrar ahora".
// Regresion del boton que "no hacia nada" (toast.confirm no existia en useToast).
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ToastProvider } from '../core/ui/ToastContext';
import { ConfirmProvider } from '../core/ui/ConfirmModal';
import { StudentMaster } from '../modules/students/StudentMaster';

vi.mock('../core/auth/useAuth', () => ({
  useAuth: () => ({ user: { id: 1, roles: ['superadmin'], is_superuser: true } }),
}));

const admitido = (dni) => ({
  admision_id: dni === '11111111' ? 1 : 2,
  dni, nombres: 'ANA', apellidos: 'PEREZ', programa_id: 2, programa_nombre: 'ENFERMERÍA TÉCNICA',
  celular: null, email_personal: null, fecha_nacimiento: '2005-03-02', modalidad: 'ORDINARIO', metadata_completa: {},
});

let resumen;
let estudiantes;
let ingestaInterrumpida;
const llamadas = [];
const json = (body, status = 200) => Promise.resolve({
  ok: status < 400, status, headers: new Headers(), json: () => Promise.resolve(body),
});

beforeEach(() => {
  llamadas.length = 0;
  resumen = { total: 2, pendientes: 2, sincronizados: 0 };
  estudiantes = [];
  ingestaInterrumpida = false;
  globalThis.fetch = vi.fn((url, opts = {}) => {
    const metodo = opts.method || 'GET';
    llamadas.push(`${metodo} ${url}`);
    if (url.includes('/mod-admision/resumen')) return json(resumen);
    if (url.includes('/mod-admision/upload')) {
      resumen = { total: 2, pendientes: 2 - estudiantes.length, sincronizados: estudiantes.length };
      return json({ total: 2, ids_pendientes: [1, 2], avisos: [] });
    }
    if (url.includes('/mod-admision/admitidos?solo_pendientes=true')) {
      return json({ admitidos: ['11111111', '22222222'].filter(dni => !estudiantes.some(e => e.dni === dni)).map(admitido) });
    }
    if (url.includes('/admision/ingesta')) {
      if (ingestaInterrumpida) return json({ detail: 'Gestión Académica no disponible.' }, 503);
      estudiantes = [
        { id: 1, dni: '11111111', nombres: 'ANA', apellidos: 'PEREZ', codigo_estudiante: '2026-11111111', programa_id: 2 },
        { id: 2, dni: '22222222', nombres: 'ANA', apellidos: 'PEREZ', codigo_estudiante: '2026-22222222', programa_id: 2 },
      ];
      resumen = { total: 0, pendientes: 0, sincronizados: 0 };
      return json({
        estudiantes_creados: 2, ya_existentes: 0, sin_programa: [], con_error: [],
        programas_sin_plan: [], programas_desalineados: [], creados_por_programa: { 2: 2 },
      }, 202);
    }
    if (url.includes('/mod-gestion-academica/estudiantes')) return json(estudiantes);
    return json([]);
  });
});

function renderVista() {
  return render(
    <ToastProvider>
      <ConfirmProvider>
        <StudentMaster />
      </ConfirmProvider>
    </ToastProvider>,
  );
}

describe('Registro de Estudiantes: sincronizar ingresantes de Admisión', () => {
  it('"Registrar ahora" confirma, registra a los pendientes y actualiza el resumen', async () => {
    renderVista();

    const banner = await screen.findByTestId('admision-pendientes-banner');
    expect(banner.textContent).toMatch(/Admisión tiene 2 ingresantes pendientes/);

    fireEvent.click(screen.getByRole('button', { name: /Registrar ahora/i }));

    // Debe abrir el modal del sistema (antes lanzaba "toast.confirm is not a function")
    expect(await screen.findByTestId('confirm-modal')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('confirm-modal-ok'));

    await waitFor(() => expect(llamadas).toContain('POST /api/mod-gestion-academica/admision/ingesta'));
    const ingesta = globalThis.fetch.mock.calls.find(([u]) => u.includes('/admision/ingesta'));
    expect(JSON.parse(ingesta[1].body).admitidos.map(a => a.dni)).toEqual(['11111111', '22222222']);

    expect(await screen.findByText(/Sincronización completa: 2 estudiantes nuevos/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId('admision-pendientes-banner')).toBeNull());
    expect(llamadas.some(l => l.includes('/admitidos/sincronizados'))).toBe(false);
  });

  it('cancelar el modal no registra nada', async () => {
    renderVista();
    await screen.findByTestId('admision-pendientes-banner');

    fireEvent.click(screen.getByRole('button', { name: /Registrar ahora/i }));
    fireEvent.click(await screen.findByTestId('confirm-modal-cancel'));

    await waitFor(() => expect(screen.queryByTestId('confirm-modal')).toBeNull());
    expect(llamadas.some(l => l.includes('/admision/ingesta'))).toBe(false);
  });

  it('permite reintentar desde Admisión si la publicación del Excel se guardó y la ingesta falló', async () => {
    ingestaInterrumpida = true;
    renderVista();

    const archivo = new File(['contenido de prueba'], 'ADMISION_2026.xlsx');
    fireEvent.change(screen.getByTestId('admision-excel-input'), { target: { files: [archivo] } });
    fireEvent.click(await screen.findByTestId('confirm-modal-ok'));
    expect(await screen.findByText(/El Excel se publicó en Admisión.*Puedes reintentar/)).toBeInTheDocument();

    ingestaInterrumpida = false;
    const boton = screen.getByRole('button', { name: /^Sincronizar desde Admisión/ });
    await waitFor(() => expect(boton).toBeEnabled());
    fireEvent.click(boton);
    fireEvent.click(await screen.findByTestId('confirm-modal-ok'));
    expect(await screen.findByText(/Sincronización completa: 2 estudiantes nuevos/)).toBeInTheDocument();
    expect(llamadas.filter(l => l === 'POST /api/mod-admision/upload')).toHaveLength(1);
    expect(llamadas.filter(l => l === 'POST /api/mod-gestion-academica/admision/ingesta')).toHaveLength(2);
  });

  it('mantiene el padrón y la bandeja vacíos después de las bajas hasta que se cargue otro archivo', async () => {
    resumen = { total: 0, pendientes: 0, sincronizados: 0 };
    renderVista();
    fireEvent.click(screen.getByRole('button', { name: /^Sincronizar desde Admisión/ }));
    expect(await screen.findByText('La bandeja de Admisión está vacía.')).toBeInTheDocument();
    expect(screen.queryByTestId('admision-pendientes-banner')).not.toBeInTheDocument();
    expect(screen.queryByTestId('confirm-modal')).not.toBeInTheDocument();
    expect(llamadas.some(l => l.includes('/admision/ingesta'))).toBe(false);
  });
});
