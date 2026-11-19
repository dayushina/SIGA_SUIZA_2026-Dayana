// Acciones de baja reutilizadas en Registro de Estudiantes, Ficha del estudiante
// y Proceso de Matricula. El backend valida rol, motivo y reglas (notas, recibos...).
import { useState } from 'react';
import { apiClient } from '../../core/api/client';
import { useAuth } from '../../core/auth/useAuth';
import { useConfirm } from '../../core/ui/ConfirmModal';
import { useToast } from '../../core/ui/ToastContext';
import { puedeDesmatricular, puedeEliminarEstudiantes } from '../../core/auth/roles';

const API = '/api/mod-gestion-academica';
const nombreDe = (s) => `${s?.nombres || ''} ${s?.apellidos || ''}`.trim() || 'el estudiante';

export function useBajasEstudiante() {
  const { user } = useAuth();
  const { promptModal } = useConfirm();
  const toast = useToast();
  const [procesando, setProcesando] = useState(null); // 'mat-<id>' | 'est-<id>'

  // Devuelve true si la matricula quedo anulada.
  const desmatricular = async (estudiante, matricula, periodoTexto = '') => {
    const motivo = await promptModal({
      title: `Desmatricular a ${nombreDe(estudiante)}`,
      message: `Se anulará su matrícula${periodoTexto ? ` del periodo ${periodoTexto}` : ''}. `
        + 'El registro se conserva para auditoría y el estudiante podrá volver a matricularse. '
        + 'No procede si ya tiene notas registradas. Si la matrícula se pagó por error, '
        + 'anule después el recibo en Finanzas y Pagos.',
      placeholder: 'Motivo (ej. matriculado por error, se retiró antes de iniciar)',
      confirmText: 'Desmatricular',
      type: 'danger',
      required: true,
    });
    if (!motivo || !motivo.trim()) return false;
    setProcesando(`mat-${matricula.id}`);
    try {
      const res = await apiClient.request(`${API}/matriculas/${matricula.id}/anular`, {
        method: 'POST',
        data: { motivo: motivo.trim() },
      });
      toast.success(res?.message || 'Matrícula anulada.');
      return true;
    } catch (e) {
      toast.error(e.message);
      return false;
    } finally {
      setProcesando(null);
    }
  };

  // Devuelve true si el estudiante fue eliminado.
  const eliminar = async (estudiante) => {
    const motivo = await promptModal({
      title: `Eliminar a ${nombreDe(estudiante)}`,
      message: `DNI ${estudiante.dni || '—'} · ${estudiante.codigo_estudiante || ''}. Esta acción es definitiva. `
        + 'Solo procede si no tiene matrícula vigente, recibos vigentes ni notas: '
        + 'primero desmatricúlelo y anule sus recibos. Queda registro de auditoría.',
      placeholder: 'Motivo (ej. registro duplicado, DNI equivocado)',
      confirmText: 'Eliminar estudiante',
      type: 'danger',
      required: true,
    });
    if (!motivo || !motivo.trim()) return false;
    setProcesando(`est-${estudiante.id}`);
    try {
      const res = await apiClient.request(
        `${API}/estudiantes/${estudiante.id}?motivo=${encodeURIComponent(motivo.trim())}`,
        { method: 'DELETE' },
      );
      toast.success(res?.message || 'Estudiante eliminado.');
      return true;
    } catch (e) {
      toast.error(e.message);
      return false;
    } finally {
      setProcesando(null);
    }
  };

  return {
    puedeDesmatricular: puedeDesmatricular(user),
    puedeEliminar: puedeEliminarEstudiantes(user),
    desmatricular,
    eliminar,
    desmatriculando: (matriculaId) => procesando === `mat-${matriculaId}`,
    eliminando: (estudianteId) => procesando === `est-${estudianteId}`,
  };
}
