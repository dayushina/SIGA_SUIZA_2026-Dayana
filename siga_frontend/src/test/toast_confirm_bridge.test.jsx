// Regresion: varios modulos llaman `await toast.confirm({...})` (Registro de Estudiantes,
// Becas, Malla Curricular, Usuarios, Pagos, TUPA). useToast() no exponia `confirm`, asi que
// el clic lanzaba "toast.confirm is not a function" y el boton "no hacia nada".
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ToastProvider, useToast } from '../core/ui/ToastContext';
import { ConfirmProvider } from '../core/ui/ConfirmModal';

function Harness({ onResult }) {
  const toast = useToast();
  return (
    <>
      <button onClick={async () => onResult(await toast.confirm({ title: 'Registrar', message: '¿Registrar 781?', confirmText: 'Registrar ahora' }))}>
        via-toast
      </button>
      <button onClick={async () => onResult(await toast.toast.confirm('¿Seguro?'))}>
        via-toast-anidado
      </button>
    </>
  );
}

afterEach(() => vi.restoreAllMocks());

describe('useToast().confirm delega en el modal del sistema', () => {
  it('abre el ConfirmModal y resuelve true al confirmar', async () => {
    const onResult = vi.fn();
    render(<ToastProvider><ConfirmProvider><Harness onResult={onResult} /></ConfirmProvider></ToastProvider>);

    fireEvent.click(screen.getByText('via-toast'));
    expect(await screen.findByTestId('confirm-modal')).toBeInTheDocument();
    expect(screen.getByText('¿Registrar 781?')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('confirm-modal-ok'));

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(true));
  });

  it('resuelve false al cancelar (forma toast.toast.confirm)', async () => {
    const onResult = vi.fn();
    render(<ToastProvider><ConfirmProvider><Harness onResult={onResult} /></ConfirmProvider></ToastProvider>);

    fireEvent.click(screen.getByText('via-toast-anidado'));
    fireEvent.click(await screen.findByTestId('confirm-modal-cancel'));

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(false));
  });

  it('sin ConfirmProvider usa el dialogo nativo en vez de fallar en silencio', async () => {
    const onResult = vi.fn();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<ToastProvider><Harness onResult={onResult} /></ToastProvider>);

    fireEvent.click(screen.getByText('via-toast'));

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(true));
    expect(window.confirm).toHaveBeenCalledWith('¿Registrar 781?');
  });
});
