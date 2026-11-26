import { useState, useEffect, useRef } from 'react';
import { apiClient } from '../../core/api/client';
import { useAuth } from '../../core/auth/useAuth';
import { tieneRol } from '../../core/auth/roles';
import { useConfirm } from '../../core/ui/ConfirmModal';
import { useToast } from '../../core/ui/ToastContext';
import { X, Calendar, Plus, Clock, Trash2, Loader2, Pencil, Save } from 'lucide-react';

const EMPTY_PERIOD = {
  codigo: '',
  fecha_inicio: '',
  fecha_fin: '',
  fecha_fin_matricula_regular: '',
  fecha_fin_matricula_extemporanea: '',
  estado: 'planificacion',
};
const EDITABLE_STATES = new Set(['planificacion', 'matricula_abierta', 'en_curso']);
const canEditStatus = (period) => EDITABLE_STATES.has(period?.estado?.trim().toLowerCase());
const inputDate = (value) => value?.slice(0, 10) ?? '';
const displayDate = (value) => value ? new Date(`${inputDate(value)}T00:00:00`).toLocaleDateString('es-PE') : '—';

export function PeriodManager({ onClose, onUpdate }) {
  const { user } = useAuth();
  const { confirm } = useConfirm();
  const toast = useToast();
  const [periods, setPeriods] = useState([]);
  const [loading, setLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [changingStatus, setChangingStatus] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [deleteError, setDeleteError] = useState(null);
  const [editing, setEditing] = useState(null);
  const [editError, setEditError] = useState('');
  const codeInput = useRef(null);
  const editTrigger = useRef(null);
  const busy = loading || isSubmitting || changingStatus || deletingId !== null;
  const canDelete = tieneRol(user);
  const canEdit = tieneRol(user, 'secretaria_academica');
  const [newPeriod, setNewPeriod] = useState(EMPTY_PERIOD);
  const editId = editing?.period.id;
  const form = editing?.data ?? newPeriod;
  const editable = canEdit && canEditStatus(periods.find(p => p.id === editId));

  useEffect(() => {
    if (editId !== undefined) codeInput.current?.focus();
    else editTrigger.current?.focus();
  }, [editId]);

  const fetchPeriods = async () => {
    setLoading(true);
    try {
      const data = await apiClient.callModule('mod-programas-estudio', 'periodos');
      setPeriods(Array.isArray(data) ? data : []);
    } catch (error) {
      console.error('Error fetching periods:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPeriods();
  }, []);

  const updateField = (name, value) => {
    if (editing) {
      const original = editing.period[name];
      const nextValue = name.startsWith('fecha_') && value === inputDate(original) ? original ?? '' : value;
      setEditing({ ...editing, data: { ...editing.data, [name]: nextValue } });
    } else {
      setNewPeriod({ ...newPeriod, [name]: value });
    }
  };

  const startEditing = (period, trigger) => {
    const { codigo, fecha_inicio, fecha_fin, fecha_fin_matricula_regular, fecha_fin_matricula_extemporanea } = period;
    editTrigger.current = trigger;
    setEditError('');
    setEditing({ period, data: { codigo, fecha_inicio, fecha_fin, fecha_fin_matricula_regular, fecha_fin_matricula_extemporanea } });
  };

  const handleSave = async (e) => {
    e.preventDefault();
    if (busy || (editing && !editable)) return;
    setIsSubmitting(true);
    setEditError('');
    try {
      if (editing) {
        const updated = await apiClient.callModule('mod-programas-estudio', `periodos/${editId}`, 'PUT', {
          ...form,
          fecha_fin_matricula_regular: form.fecha_fin_matricula_regular || null,
          fecha_fin_matricula_extemporanea: form.fecha_fin_matricula_extemporanea || null,
          updated_at: editing.period.updated_at ?? null,
        });
        if (updated?.id !== editId) throw new Error('No se pudo confirmar la actualización del periodo.');
        setPeriods(prev => prev.map(p => p.id === editId ? updated : p)
          .sort((a, b) => b.fecha_inicio.localeCompare(a.fecha_inicio)));
        setEditing(null);
        toast.success('Periodo académico actualizado');
      } else {
        await apiClient.callModule('mod-programas-estudio', 'periodos', 'POST', newPeriod);
        setNewPeriod(EMPTY_PERIOD);
        await fetchPeriods();
        toast.success('Periodo académico creado exitosamente');
      }
      onUpdate?.();
    } catch (error) {
      if (editing) {
        setEditError(error.message || 'No se pudo actualizar el periodo.');
        await fetchPeriods();
      } else {
        toast.error(error.message || 'Hubo un error al crear el periodo');
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const getStatusBadge = (estado) => {
    switch (estado?.toLowerCase()) {
      case 'planificacion':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-amber-50 text-amber-700 border border-amber-200/60">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-500"></span>
            Planificación
          </span>
        );
      case 'matricula_abierta':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-sky-50 text-sky-700 border border-sky-200/60">
            <span className="w-1.5 h-1.5 rounded-full bg-sky-500 animate-pulse"></span>
            Matrícula Abierta
          </span>
        );
      case 'en_curso':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200/60">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
            En Curso
          </span>
        );
      case 'historico':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-slate-100 text-slate-600 border border-slate-200">
            <span className="w-1.5 h-1.5 rounded-full bg-slate-400"></span>
            Histórico
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-slate-100 text-slate-600 border border-slate-200">
            <span className="w-1.5 h-1.5 rounded-full bg-slate-400"></span>
            {estado}
          </span>
        );
    }
  };

  const handleStatusChange = async (periodoId, nuevoEstado) => {
    if (busy) return;
    setChangingStatus(true);
    try {
      await apiClient.callModule('mod-programas-estudio', `periodos/${periodoId}/estado?estado=${nuevoEstado}`, 'PUT');
      await fetchPeriods();
      if (onUpdate) onUpdate();
      toast.success(`Estado del periodo actualizado a "${nuevoEstado}"`);
    } catch (error) {
      console.error('Error actualizando estado:', error);
      toast.error(error.message || 'Error al actualizar el estado del periodo');
    } finally {
      setChangingStatus(false);
    }
  };

  const handleDelete = async (period) => {
    if (!canDelete || busy) return;
    setDeletingId(period.id);
    setDeleteError(null);
    try {
      const confirmed = await confirm({
        title: `Eliminar el periodo ${period.codigo}`,
        message: 'El periodo y sus fechas se retirarán del calendario de forma permanente. El sistema comprobará si hay registros vinculados.',
        confirmText: 'Eliminar periodo',
        isDestructive: true,
      });
      if (!confirmed) return;
      const result = await apiClient.callModule('mod-programas-estudio', `periodos/${period.id}`, 'DELETE');
      if (result?.id !== period.id) throw new Error('No se pudo confirmar la eliminación. Actualiza el calendario.');
      setPeriods(prev => prev.filter(p => p.id !== period.id));
      onUpdate?.();
      toast.success(result.message);
    } catch (error) {
      setDeleteError({ id: period.id, message: error.message || 'No se pudo eliminar el periodo.' });
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/40 backdrop-blur-sm animate-fade-in">
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden">
        {/* Modal Header */}
        <div className="flex justify-between items-center px-6 py-4 border-b border-slate-100">
          <div>
            <h5 className="font-bold text-base text-slate-900 tracking-tight">Periodos y Calendario Académico</h5>
            <p className="text-xs text-slate-400">Control de cronogramas y ventanas de matrícula regular/extemporánea</p>
          </div>
          <button 
            onClick={onClose} 
            disabled={deletingId !== null || isSubmitting}
            className="text-slate-400 hover:text-slate-700 p-1.5 rounded-lg hover:bg-slate-100 transition-colors disabled:opacity-50"
            aria-label="Cerrar"
          >
            <X size={18} />
          </button>
        </div>
        
        <div className="flex flex-col md:flex-row flex-grow overflow-y-auto md:overflow-hidden">
          {/* Formulario Lateral */}
          <div className="w-full md:w-5/12 p-6 bg-slate-50/60 border-r border-slate-100 md:overflow-y-auto">
            <div className="flex items-center gap-1.5 mb-4">
              <span className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">
                {editing ? `Editar periodo ${editing.period.codigo}` : 'Crear Periodo'}
              </span>
            </div>

            <form onSubmit={handleSave} className="space-y-3.5">
              <div>
                <label htmlFor="period-code" className="block text-[11px] font-semibold text-slate-600 uppercase tracking-wider mb-1">Código del Periodo</label>
                <input 
                  id="period-code"
                  ref={codeInput}
                  type="text" 
                  className="w-full px-3 py-2 bg-white border border-slate-200 rounded-xl text-xs text-slate-800 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all font-mono uppercase" 
                  value={form.codigo}
                  onChange={e => updateField('codigo', e.target.value)}
                  maxLength={20}
                  disabled={isSubmitting}
                  required 
                  placeholder="Ej. 2026-I"
                />
              </div>

              <div className="grid grid-cols-2 gap-2.5">
                <div>
                  <label htmlFor="period-start" className="block text-[11px] font-semibold text-slate-600 uppercase tracking-wider mb-1">Fecha Inicio</label>
                  <input 
                    id="period-start"
                    type="date" 
                    className="w-full px-2.5 py-2 bg-white border border-slate-200 rounded-xl text-xs text-slate-700 focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all" 
                    value={inputDate(form.fecha_inicio)}
                    onChange={e => updateField('fecha_inicio', e.target.value)}
                    disabled={isSubmitting}
                    required 
                  />
                </div>
                <div>
                  <label htmlFor="period-end" className="block text-[11px] font-semibold text-slate-600 uppercase tracking-wider mb-1">Fecha Fin</label>
                  <input 
                    id="period-end"
                    type="date" 
                    className="w-full px-2.5 py-2 bg-white border border-slate-200 rounded-xl text-xs text-slate-700 focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all" 
                    value={inputDate(form.fecha_fin)}
                    onChange={e => updateField('fecha_fin', e.target.value)}
                    disabled={isSubmitting}
                    required 
                  />
                </div>
              </div>

              <div>
                <label htmlFor="period-regular" className="block text-[11px] font-semibold text-slate-600 uppercase tracking-wider mb-1">Cierre Matrícula Regular</label>
                <input 
                  id="period-regular"
                  type="date" 
                  className="w-full px-2.5 py-2 bg-white border border-slate-200 rounded-xl text-xs text-slate-700 focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all" 
                  value={inputDate(form.fecha_fin_matricula_regular)}
                  onChange={e => updateField('fecha_fin_matricula_regular', e.target.value)}
                  disabled={isSubmitting}
                  required={!editing}
                />
              </div>

              <div>
                <label htmlFor="period-late" className="block text-[11px] font-semibold text-slate-600 uppercase tracking-wider mb-1">Cierre Matrícula Extemporánea</label>
                <input 
                  id="period-late"
                  type="date" 
                  className="w-full px-2.5 py-2 bg-white border border-slate-200 rounded-xl text-xs text-slate-700 focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all" 
                  value={inputDate(form.fecha_fin_matricula_extemporanea)}
                  onChange={e => updateField('fecha_fin_matricula_extemporanea', e.target.value)}
                  disabled={isSubmitting}
                  required={!editing}
                />
              </div>

              {!editing && <div>
                <label htmlFor="period-initial-status" className="block text-[11px] font-semibold text-slate-600 uppercase tracking-wider mb-1">Estado Inicial</label>
                <select 
                  id="period-initial-status"
                  className="w-full px-3 py-2 bg-white border border-slate-200 rounded-xl text-xs text-slate-700 focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all" 
                  value={newPeriod.estado} 
                  onChange={e => updateField('estado', e.target.value)}
                  disabled={isSubmitting}
                >
                  <option value="planificacion">Planificación</option>
                  <option value="matricula_abierta">Matrícula Abierta</option>
                  <option value="en_curso">En Curso</option>
                </select>
              </div>}

              {editError && <p role="alert" className="rounded-lg border border-rose-100 bg-rose-50 px-3 py-2 text-xs text-rose-800">{editError}</p>}
              <div className="mt-2 flex flex-wrap gap-2">
                {editing && (
                  <button type="button" onClick={() => { setEditing(null); setEditError(''); }} disabled={isSubmitting}
                    className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-xs font-semibold text-slate-700 hover:bg-slate-100 disabled:opacity-50">
                    Cancelar
                  </button>
                )}
                <button type="submit" disabled={busy || Boolean(editing && !editable)}
                  className="flex flex-1 items-center justify-center gap-1.5 rounded-xl bg-slate-900 px-3 py-2.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-slate-800 disabled:opacity-50">
                  {isSubmitting ? <Loader2 size={15} className="animate-spin" /> : editing ? <Save size={15} /> : <Plus size={15} />}
                  {isSubmitting ? 'Guardando…' : editing ? 'Guardar cambios' : 'Registrar Periodo'}
                </button>
              </div>
            </form>
            
            <div className="mt-5 p-3.5 rounded-xl bg-slate-100/70 border border-slate-200/80 text-[11px] text-slate-600 leading-relaxed">
              <span className="font-bold text-slate-800 block mb-1">Información de Ciclos:</span>
              El estado <strong>Matrícula Abierta</strong> habilita el registro de inscripciones en el portal. Al pasar a <strong>En Curso</strong>, el motor restringe la matrícula regular.
            </div>
          </div>
          
          {/* Lista Central */}
          <div className="w-full md:w-7/12 p-6 md:overflow-y-auto">
            <div className="flex justify-between items-center mb-4">
              <span className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">Historial de Periodos</span>
              <span className="text-[11px] font-semibold text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full">
                {periods.length} registrados
              </span>
            </div>
            
            {loading ? (
              <div className="flex flex-col items-center justify-center py-16">
                <div className="w-6 h-6 border-2 border-slate-900 border-t-transparent rounded-full animate-spin mb-2"></div>
                <p className="text-slate-400 text-xs">Cargando cronogramas...</p>
              </div>
            ) : periods.length === 0 ? (
              <div className="text-center py-12 border border-dashed border-slate-200 rounded-xl">
                <div className="w-10 h-10 mx-auto mb-2 rounded-xl bg-slate-100 text-slate-400 flex items-center justify-center">
                  <Calendar size={20} />
                </div>
                <p className="text-slate-400 text-xs">No hay periodos registrados aún.</p>
              </div>
            ) : (
              <div className="space-y-2.5">
                {periods.map(period => (
                  <div 
                    key={period.id} 
                    className={`rounded-xl border p-3.5 transition-all ${editId === period.id ? 'border-sky-300 bg-sky-50/50' : 'border-slate-200/80 bg-white hover:border-slate-300 hover:shadow-sm'}`}
                  >
                    <div className="flex flex-wrap justify-between items-start gap-3">
                      <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-xl bg-slate-100 text-slate-800 font-bold text-sm flex items-center justify-center border border-slate-200/60 font-mono">
                          {period.codigo.split('-')[1] || period.codigo}
                        </div>
                        <div>
                          <h4 className="font-bold text-sm text-slate-900 tracking-tight">{period.codigo}</h4>
                          <div className="text-[11px] text-slate-400 flex items-center gap-1.5 mt-0.5">
                            <Clock size={12} className="text-slate-400" />
                            <span>
                              {displayDate(period.fecha_inicio)}
                            </span>
                            <span>→</span>
                            <span>
                              {displayDate(period.fecha_fin)}
                            </span>
                          </div>
                        </div>
                      </div>

                      <div className="flex flex-col items-end gap-2">
                        {getStatusBadge(period.estado)}
                        
                        <div className="flex flex-wrap items-center justify-end gap-1.5">
                          <select 
                            className="text-[11px] font-semibold text-slate-700 bg-slate-50 hover:bg-slate-100 border border-slate-200 rounded-lg px-2 py-0.5 cursor-pointer outline-none transition-colors"
                            aria-label={`Estado de ${period.codigo}`}
                            value={period.estado}
                            disabled={busy || Boolean(editing)}
                            onChange={(e) => handleStatusChange(period.id, e.target.value)}
                          >
                            {!['planificacion', 'matricula_abierta', 'en_curso', 'historico'].includes(period.estado) && (
                              <option value={period.estado}>{period.estado === 'cerrado' ? 'Cerrado' : period.estado}</option>
                            )}
                            <option value="planificacion">Planificación</option>
                            <option value="matricula_abierta">Matrícula Abierta</option>
                            <option value="en_curso">En Curso</option>
                            <option value="historico">Histórico</option>
                          </select>
                          {canEdit && canEditStatus(period) && (
                            <button type="button" onClick={e => startEditing(period, e.currentTarget)}
                              disabled={busy || Boolean(editing)}
                              aria-label={`Editar periodo ${period.codigo}`}
                              className="inline-flex min-h-8 items-center justify-center gap-1.5 rounded-lg border border-sky-200 bg-white px-2.5 py-1 text-xs font-semibold text-sky-800 transition-colors hover:bg-sky-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 disabled:opacity-50">
                              <Pencil size={13} />
                              Editar
                            </button>
                          )}
                          {canDelete && (
                            <button
                              type="button"
                              onClick={() => handleDelete(period)}
                              disabled={busy || Boolean(editing)}
                              aria-label={`Eliminar periodo ${period.codigo}`}
                              className="inline-flex min-h-8 items-center justify-center gap-1.5 rounded-lg border border-rose-200 bg-white px-2.5 py-1 text-xs font-semibold text-rose-700 transition-colors hover:bg-rose-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-600 disabled:opacity-50"
                            >
                              {deletingId === period.id ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                              Eliminar
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                    {deleteError?.id === period.id && (
                      <p className="mt-3 rounded-lg border border-rose-100 bg-rose-50 px-3 py-2 text-xs text-rose-800" role="alert">
                        {deleteError.message}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
