/* eslint-disable react-hooks/set-state-in-effect */
import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '../../core/auth/useAuth';
import { apiClient } from '../../core/api/client';
import { useToast } from '../../core/ui/ToastContext';
import { useConfirm } from '../../core/ui/ConfirmModal';

import { CoordinatorReview } from './CoordinatorReview';
import { CoordinatorTutorias } from './CoordinatorTutorias';
import { CoordinatorActas } from './CoordinatorActas';
import { ScheduleBuilder } from './ScheduleBuilder';
import { GraduationCap, Calendar, BookOpen, ClipboardCheck, UserCog, Clock, X, ClipboardList, BarChart3, ExternalLink, Trash2, Download, Copy, AlertTriangle, KeyRound, FileCheck } from 'lucide-react';
import { AssignmentModal } from './AssignmentModal';

function useSafeToast() {
  try {
    return useToast();
  } catch {
    const noop = () => {};
    return {
      toast: { success: noop, error: noop, warning: noop, info: noop, dismiss: noop, clear: noop },
      success: noop,
      error: noop,
      warning: noop,
      info: noop,
      showSuccess: noop,
      showError: noop,
      showWarning: noop,
      showInfo: noop,
      dismiss: noop,
      clear: noop,
      toasts: [],
    };
  }
}

function useSafeConfirm() {
  try {
    return useConfirm();
  } catch {
    const fn = async () => true;
    return Object.assign(fn, {
      confirm: async () => true,
      promptModal: async () => '',
      prompt: async () => '',
    });
  }
}

export function CoordinatorAcademic() {
  const { user, openPasswordModal } = useAuth();
  const { toast } = useSafeToast();
  const { confirm } = useSafeConfirm();

  const [activeTab, setActiveTab] = useState('carga'); // 'carga', 'horarios'
  const [horariosSubTab, setHorariosSubTab] = useState('visual'); // 'excel', 'visual'
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  // Selectors
  const [programs, setPrograms] = useState([]);
  const [periods, setPeriods] = useState([]);
  const [selectedProgram, setSelectedProgram] = useState('');
  const [selectedPeriod, setSelectedPeriod] = useState('');

  // Data
  const [ofertaDocente, setOfertaDocente] = useState(null);
  const cargaSolicitud = useRef(0);
  const contexto = selectedProgram + '/' + selectedPeriod;
  const docentes = ofertaDocente?.contexto === contexto ? ofertaDocente.docentes : [];
  const estadoDocentes = ofertaDocente?.contexto === contexto ? ofertaDocente.estado : 'cargando';
  const [unidadesFlat, setUnidadesFlat] = useState([]);
  const [cargaLectiva, setCargaLectiva] = useState([]);
  const [horarios, setHorarios] = useState([]);

  // Forms
  const [cargaForm, setCargaForm] = useState({ docente_id: '', unidades_didacticas_ids: [], turno: 'Mañana', seccion: 'A' });
  const [horarioForm, setHorarioForm] = useState({ archivo_excel_url: '', observaciones: '' });
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [showCloneModal, setShowCloneModal] = useState(false);
  const [cloneFromPeriod, setCloneFromPeriod] = useState('');

  const userRoles = (Array.isArray(user?.roles) ? user.roles : [user?.role])
    .map(role => typeof role === 'string' ? role : role?.name ?? role?.nombre);
  const isCoordinador = !user?.is_superuser && !userRoles.includes('superadmin') && userRoles.includes('coordinador_programa');
  const programaAsignado = user?.programa_id;

  useEffect(() => {
    if (!user?.id) return;
    let activo = true;
    Promise.all([
      apiClient.request('/api/mod-programas-estudio/programas?implementados=true').catch(() => []),
      apiClient.request('/api/mod-programas-estudio/periodos').catch(() => []),
    ]).then(([programas, periodos]) => {
      if (!activo) return;
      const catalogo = Array.isArray(programas) ? programas : [];
      const disponibles = isCoordinador
        ? catalogo.filter(p => String(p.id) === String(programaAsignado))
        : catalogo;
      const periodosValidos = Array.isArray(periodos) ? periodos : [];
      setPrograms(disponibles);
      setPeriods(periodosValidos);
      const programaInicial = disponibles.find(p => p.id === programaAsignado) ?? disponibles[0];
      setSelectedProgram(programaInicial ? String(programaInicial.id) : '');
      const periodoActivo = periodosValidos.find(p => ['activo', 'matricula_abierta', 'en_curso'].includes(p.estado?.toLowerCase())) ?? periodosValidos[0];
      setSelectedPeriod(periodoActivo ? String(periodoActivo.id) : '');
    });
    return () => { activo = false; };
  }, [user?.id, isCoordinador, programaAsignado]);

  useEffect(() => {
    setUnidadesFlat([]);
    if (!selectedProgram) return;
    let activo = true;
    apiClient.request(`/api/mod-programas-estudio/programas/${selectedProgram}/malla`)
      .then(modulos => {
        if (!activo) return;
        setUnidadesFlat((modulos || []).flatMap(modulo => (modulo.unidades || []).map(ud => ({
          ...ud, modulo_nombre: modulo.nombre, periodo_sugerido: modulo.periodo,
        }))));
      })
      .catch(error => console.error('Error al consultar la malla', error));
    return () => { activo = false; };
  }, [selectedProgram]);

  useEffect(() => {
    setCargaForm(prev => ({ ...prev, docente_id: '', unidades_didacticas_ids: [] }));
    setIsModalOpen(false);
    setOfertaDocente(null);
    if (!selectedProgram || !selectedPeriod) return;
    const controller = new AbortController();
    apiClient.request(`/api/mod-programas-estudio/programas/${selectedProgram}/docentes-disponibles?periodo_id=${selectedPeriod}`, {
      signal: controller.signal, cache: 'no-store',
    }).then(data => {
      if (controller.signal.aborted) return;
      if (!Array.isArray(data)) throw new Error('Respuesta de docentes no válida');
      setOfertaDocente({ contexto, docentes: data, estado: 'listo' });
    }).catch(() => {
      if (!controller.signal.aborted) setOfertaDocente({ contexto, docentes: [], estado: 'error' });
    });
    return () => controller.abort();
  }, [selectedProgram, selectedPeriod, contexto]);

  const fetchCargaYHorarios = useCallback(async () => {
    const solicitud = ++cargaSolicitud.current;
    setLoading(true);
    try {
      const [cargaRes, horRes] = await Promise.all([
        apiClient.request(`/api/mod-programas-estudio/programas/${selectedProgram}/carga-lectiva?periodo_id=${selectedPeriod}`).catch(() => []),
        apiClient.request(`/api/mod-programas-estudio/programas/${selectedProgram}/horarios?periodo_id=${selectedPeriod}`).catch(() => [])
      ]);
      if (solicitud !== cargaSolicitud.current) return;
      setCargaLectiva(cargaRes || []);
      setHorarios(horRes || []);
    } catch (error) {
      console.error("Error fetching data", error);
    } finally {
      if (solicitud === cargaSolicitud.current) setLoading(false);
    }
  }, [selectedProgram, selectedPeriod]);

  // 3. Load Carga & Horarios when Program AND Period change
  useEffect(() => {
    setCargaLectiva([]);
    setHorarios([]);
    if (!selectedProgram || !selectedPeriod) {
      setLoading(false);
      return;
    }
    fetchCargaYHorarios();
    return () => { cargaSolicitud.current += 1; };
  }, [selectedProgram, selectedPeriod, fetchCargaYHorarios]);

  const handleCreateCarga = async (e) => {
    e.preventDefault();
    if (!docentes.some(d => String(d.id) === cargaForm.docente_id)) return;

    setSaving(true);
    try {
      const promises = cargaForm.unidades_didacticas_ids.map(ud_id => {
        const payload = {
          periodo_id: parseInt(selectedPeriod),
          docente_id: parseInt(cargaForm.docente_id),
          unidad_didactica_id: parseInt(ud_id),
          turno: cargaForm.turno,
          seccion: cargaForm.seccion,
          estado: 'publicado' // auto-publish for MVP
        };
        return apiClient.request(`/api/mod-programas-estudio/programas/${selectedProgram}/carga-lectiva`, {
          method: 'POST',
          body: JSON.stringify(payload)
        });
      });

      await Promise.all(promises);
      setCargaForm({ ...cargaForm, unidades_didacticas_ids: [] });
      setIsModalOpen(false);
      fetchCargaYHorarios();
      toast.success("Docente asignado correctamente a los cursos seleccionados.");
    } catch (error) {
      console.error("Error saving carga", error);
      toast.error(error.message || "Error al guardar la carga lectiva.");
    } finally {
      setSaving(false);
    }
  };

  const handleUploadHorario = async (e) => {
    e.preventDefault();

    setSaving(true);
    try {
      const payload = {
        periodo_id: parseInt(selectedPeriod),
        archivo_excel_url: horarioForm.archivo_excel_url,
        observaciones: horarioForm.observaciones,
        estado: 'publicado'
      };
      await apiClient.request(`/api/mod-programas-estudio/programas/${selectedProgram}/horarios`, {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      setHorarioForm({ archivo_excel_url: '', observaciones: '' });
      toast.success("Horario publicado correctamente");
      fetchCargaYHorarios();
    } catch (error) {
      console.error("Error saving horario", error);
      toast.error("Error al subir el horario");
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteCarga = async (id) => {
    const ok = await confirm({
      title: "Eliminar asignación",
      message: "¿Seguro que desea eliminar esta asignación?",
      isDestructive: true
    });
    if (!ok) return;
    try {
      await apiClient.request(`/api/mod-programas-estudio/programas/${selectedProgram}/carga-lectiva/${id}`, { method: 'DELETE' });
      toast.success("Asignación de carga lectiva eliminada");
      fetchCargaYHorarios();
    } catch (error) {
      console.error("Error deleting carga", error);
      toast.error("No se pudo eliminar la carga");
    }
  };

  const handleDeleteHorario = async (id) => {
    const ok = await confirm({
      title: "Eliminar horario",
      message: "¿Seguro que desea eliminar este horario?",
      isDestructive: true
    });
    if (!ok) return;
    try {
      await apiClient.request(`/api/mod-programas-estudio/programas/${selectedProgram}/horarios/${id}`, { method: 'DELETE' });
      toast.success("Horario eliminado");
      fetchCargaYHorarios();
    } catch (error) {
      console.error("Error deleting horario", error);
      toast.error("No se pudo eliminar el horario");
    }
  };

  const getDocenteName = (id) => {
    return docentes.find(d => d.id === id)?.full_name
      || cargaLectiva.find(c => c.docente_id === id)?.docente_nombre
      || `Docente #${id}`;
  };

  const getUDName = (id) => {
    const ud = unidadesFlat.find(u => u.id === id);
    return ud ? ud.nombre : `ID: ${id}`;
  };

  const handleExportCSV = () => {
    const headers = "Unidad Didactica,Docente,Turno,Seccion,Estado\n";
    const rows = cargaLectiva.map(c => `"${getUDName(c.unidad_didactica_id)}","${getDocenteName(c.docente_id)}",${c.turno},${c.seccion},${c.estado}`).join('\n');
    const blob = new Blob(["\uFEFF" + headers + rows], { type: 'text/csv;charset=utf-8;' }); // \uFEFF para soportar caracteres UTF-8 en Excel
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.setAttribute("href", url);
    link.setAttribute("download", `Carga_Lectiva_${selectedProgram}_${selectedPeriod}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleOpenCloneModal = () => {
    if (!selectedProgram || !selectedPeriod) {
      toast.warning("Seleccione primero un programa y periodo actual.");
      return;
    }
    setCloneFromPeriod('');
    setShowCloneModal(true);
  };

  const handleExecuteCloneCarga = async () => {
    if (!cloneFromPeriod) {
      toast.warning("Seleccione el periodo de origen.");
      return;
    }
    setLoading(true);
    try {
      const res = await apiClient.request(`/api/mod-programas-estudio/programas/${selectedProgram}/carga-lectiva/clonar`, {
        method: 'POST',
        body: JSON.stringify({ from_periodo_id: parseInt(cloneFromPeriod), to_periodo_id: parseInt(selectedPeriod) })
      });
      toast.success(res.message || "Carga lectiva clonada correctamente");
      setShowCloneModal(false);
      fetchCargaYHorarios();
    } catch (error) {
      toast.error(error.message || "Error al clonar la carga lectiva.");
    } finally {
      setLoading(false);
    }
  };

  const periodoCerrado = ['cerrado', 'finalizado'].includes(periods.find(p => String(p.id) === selectedPeriod)?.estado?.toLowerCase());
  const avisoDocentes = periodoCerrado ? 'El periodo está cerrado para nuevas asignaciones.'
    : estadoDocentes === 'error' ? 'No se pudo consultar la disponibilidad docente.'
    : estadoDocentes === 'cargando' ? 'Consultando docentes…'
    : docentes.length === 0 ? 'No hay docentes habilitados para este programa y periodo.' : '';
  const docentesHistoricos = [
    ...docentes,
    ...cargaLectiva.filter(c => c.docente_nombre && !docentes.some(d => d.id === c.docente_id))
      .map(c => ({ id: c.docente_id, full_name: c.docente_nombre })),
  ];

  return (
    <div className="space-y-6 animate-fade-in pb-12">
      <AssignmentModal 
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        docenteId={cargaForm.docente_id}
        docenteName={getDocenteName(parseInt(cargaForm.docente_id))}
        periodoObj={periods.find(p => p.id == selectedPeriod)}
        unidadesFlat={unidadesFlat}
        cargaForm={cargaForm}
        setCargaForm={setCargaForm}
        handleCreateCarga={handleCreateCarga}
        saving={saving}
        teacherLoad={cargaLectiva.filter(c => c.docente_id === parseInt(cargaForm.docente_id)).length}
        isOverloaded={cargaLectiva.filter(c => c.docente_id === parseInt(cargaForm.docente_id)).length >= 4}
      />

      <div className="glass-panel p-6 mb-8 flex justify-between items-center bg-gradient-to-r from-primary-soft to-primary-soft/40">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-slate-text">
            Coordinación Académica
          </h2>
          <p className="text-slate-500 text-sm mt-1">
            Gestión de Carga Lectiva y Horarios del Programa de Estudios
          </p>
        </div>
        <div>
          <button
            type="button"
            onClick={openPasswordModal}
            className="inline-flex items-center gap-2 px-3.5 py-2 rounded-xl bg-white border border-slate-200 text-slate-700 hover:text-primary hover:bg-slate-50 text-xs font-bold shadow-sm transition-all cursor-pointer"
            title="Configuración de seguridad y cambio de contraseña"
          >
            <KeyRound size={15} className="text-primary" />
            <span>Seguridad / Clave</span>
          </button>
        </div>
      </div>

      {/* Contexto Operativo Informativo */}
      <div className="glass-card p-4 flex flex-col md:flex-row gap-4 items-start md:items-center justify-between border-l-4 border-l-primary mb-8 bg-gradient-to-r from-white to-slate-50">
        <div className="flex items-center space-x-3 w-full md:w-auto flex-1">
          <div className="w-10 h-10 rounded-full bg-primary/10 text-primary flex-shrink-0 flex items-center justify-center">
            <GraduationCap size={20} />
          </div>
          <div className="w-full">
            <p className="text-[10px] uppercase font-extrabold text-slate-400 tracking-wider">Programa de Estudios</p>
            {isCoordinador ? (
              <div className="w-full bg-transparent font-bold text-slate-800 border-none p-0 text-sm mt-1">
                {programs.find(p => p.id.toString() === selectedProgram)?.nombre || "Programa implementado pendiente de asignar"}
              </div>
            ) : (
              <select 
                aria-label="Programa de estudios"
                disabled={saving}
                className="w-full bg-transparent font-bold text-slate-800 border-none p-0 focus:ring-0 cursor-pointer text-sm"
                value={selectedProgram}
                onChange={(e) => setSelectedProgram(e.target.value)}
              >
                <option value="" disabled>Seleccione un programa...</option>
                {programs.map(p => (
                  <option key={p.id} value={p.id.toString()}>{p.nombre}</option>
                ))}
              </select>
            )}
          </div>
        </div>
        <div className="h-8 w-px bg-slate-200 hidden md:block"></div>
        <div className="flex items-center space-x-3 w-full md:w-auto">
          <div className="w-10 h-10 rounded-full bg-primary-soft text-primary flex-shrink-0 flex items-center justify-center">
            <Calendar size={20} />
          </div>
          <div className="w-full">
            <p className="text-[10px] uppercase font-extrabold text-slate-400 tracking-wider">Periodo lectivo</p>
            <select
              aria-label="Periodo lectivo"
              disabled={saving}
              className="w-full bg-transparent font-bold text-slate-800 border-none p-0 focus:ring-0 cursor-pointer text-sm"
              value={selectedPeriod}
              onChange={e => setSelectedPeriod(e.target.value)}
            >
              <option value="" disabled>Seleccione un periodo...</option>
              {periods.map(p => <option key={p.id} value={String(p.id)}>{p.codigo}</option>)}
            </select>
          </div>
        </div>
      </div>

      {selectedProgram && selectedPeriod && (
        <>
          {/* Métricas Rápidas */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
            <div className="glass-card p-5 flex items-center justify-between border-l-4 border-l-blue-500 bg-gradient-to-r from-blue-50/50 to-white">
              <div>
                <p className="text-[10px] text-slate-500 font-extrabold uppercase tracking-widest mb-1">Cursos Asignados</p>
                <p className="text-3xl font-black text-slate-800 flex items-baseline gap-2">
                  {cargaLectiva.length} 
                  <span className="text-sm font-bold text-slate-400">/ {unidadesFlat.length}</span>
                </p>
              </div>
              <div className="w-12 h-12 rounded-2xl bg-blue-100/50 flex items-center justify-center text-blue-600 shadow-sm">
                <BookOpen size={24} />
              </div>
            </div>

            <div className="glass-card p-5 flex items-center justify-between border-l-4 border-l-emerald-500 bg-gradient-to-r from-emerald-50/50 to-white">
              <div>
                <p className="text-[10px] text-slate-500 font-extrabold uppercase tracking-widest mb-1">Docentes Asignados</p>
                <p className="text-3xl font-black text-slate-800 flex items-baseline gap-2">
                  {new Set(cargaLectiva.map(c => c.docente_id)).size}
                  <span className="text-sm font-bold text-slate-400">docentes</span>
                </p>
              </div>
              <div className="w-12 h-12 rounded-2xl bg-emerald-100/50 flex items-center justify-center text-emerald-600 shadow-sm">
                <UserCog size={24} />
              </div>
            </div>
          </div>

          {/* Tabs */}
          <div className="flex space-x-2 border-b border-slate-200 mb-6 overflow-x-auto pb-1">
            <button
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap flex items-center gap-2 ${activeTab === 'carga' ? 'border-primary text-primary' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300'}`}
              onClick={() => setActiveTab('carga')}
            >
              <BookOpen size={16} /> Carga Lectiva (Asignación)
            </button>
            <button
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap flex items-center gap-2 ${activeTab === 'revision' ? 'border-primary text-primary' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300'}`}
              onClick={() => setActiveTab('revision')}
            >
              <ClipboardCheck size={16} /> Revisión (Sílabos y Planes)
            </button>
            <button
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap flex items-center gap-2 ${activeTab === 'tutorias' ? 'border-primary text-primary' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300'}`}
              onClick={() => setActiveTab('tutorias')}
            >
              <UserCog size={16} /> Asignar Tutorías
            </button>
            <button
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap flex items-center gap-2 ${activeTab === 'horarios' ? 'border-primary text-primary' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300'}`}
              onClick={() => setActiveTab('horarios')}
            >
              <Clock size={16} /> Horarios del Periodo
            </button>
            <button
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap flex items-center gap-2 ${activeTab === 'actas' ? 'border-primary text-primary' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300'}`}
              onClick={() => setActiveTab('actas')}
              data-testid="tab-control-actas"
            >
              <FileCheck size={16} /> Control de Actas
            </button>
          </div>

          {loading ? (
            <div className="text-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-4 border-primary border-t-transparent mx-auto"></div>
              <p className="mt-4 text-slate-500 text-sm">Cargando datos del periodo...</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">

              {/* Carga Lectiva Tab */}
              {activeTab === 'carga' && (
                <>
                  <div className="lg:col-span-1">
                    <form className="glass-card p-6 sticky top-6 mb-6">
                      <h3 className="text-lg font-semibold text-slate-text mb-4">Nueva Asignación</h3>

                      <div className="space-y-4">
                        <div>
                          <label className="label" htmlFor="coordinacion-docente">Docente</label>
                          <select 
                            id="coordinacion-docente"
                            required
                            disabled={periodoCerrado || estadoDocentes !== 'listo' || docentes.length === 0}
                            className="input-field w-full"
                            value={cargaForm.docente_id}
                            onChange={e => setCargaForm({...cargaForm, docente_id: e.target.value})}
                          >
                            <option value="">Seleccione Docente</option>
                            {docentes.map(d => (
                              <option key={d.id} value={d.id}>{d.full_name || d.email}</option>
                            ))}
                          </select>
                          {avisoDocentes && <p className="mt-2 text-xs text-slate-600" role="status">{avisoDocentes}</p>}
                        </div>

                        {cargaForm.docente_id && (
                          <button 
                            type="button" 
                            onClick={() => setIsModalOpen(true)}
                            className="w-full py-2 px-4 border border-primary-light rounded-xl shadow-sm text-sm font-bold text-primary bg-primary-soft hover:bg-primary/10 transition-colors flex items-center justify-center gap-2"
                          >
                            <ClipboardList size={16} /> Seleccionar Cursos ({cargaForm.unidades_didacticas_ids.length} seleccionados)
                          </button>
                        )}
                      </div>
                    </form>

                    {/* Botón de Clonar */}
                    <div className="glass-card p-5 bg-gradient-to-br from-indigo-50 to-white border border-indigo-100">
                      <h4 className="text-sm font-bold text-indigo-900 mb-2 flex items-center gap-2"><Copy size={16}/> Ahorra tiempo</h4>
                      <p className="text-xs text-indigo-700/80 mb-4 leading-relaxed">¿Mismos cursos y docentes que el ciclo anterior? Copia toda la carga lectiva con un clic.</p>
                      <button 
                        onClick={handleOpenCloneModal}
                        className="w-full py-2 px-4 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-sm font-bold shadow-sm transition-colors flex justify-center items-center gap-2"
                      >
                        Clonar periodo anterior
                      </button>
                    </div>
                  </div>

                  <div className="lg:col-span-2">
                    <div className="glass-card p-6 overflow-hidden">
                      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center mb-4 gap-4">
                        <h3 className="text-lg font-semibold text-slate-text">Carga Lectiva Actual</h3>
                        {cargaLectiva.length > 0 && (
                          <button 
                            onClick={handleExportCSV}
                            className="text-sm font-semibold text-emerald-700 bg-emerald-50 hover:bg-emerald-100 px-3 py-1.5 rounded-lg flex items-center gap-2 transition-colors border border-emerald-200"
                          >
                            <Download size={16} /> Exportar CSV
                          </button>
                        )}
                      </div>
                      {cargaLectiva.length === 0 ? (
                        <div className="text-center py-10 text-slate-400 bg-slate-50/50 rounded-xl border border-dashed border-slate-200">
                          No hay asignaciones para este periodo.
                        </div>
                      ) : (
                        <div className="overflow-x-auto">
                          <table className="min-w-full divide-y divide-slate-200">
                            <thead>
                              <tr>
                                <th className="px-4 py-3 bg-slate-50 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Unidad Didáctica</th>
                                <th className="px-4 py-3 bg-slate-50 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Docente</th>
                                <th className="px-4 py-3 bg-slate-50 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Detalles</th>
                                <th className="px-4 py-3 bg-slate-50 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Estado</th>
                                <th className="px-4 py-3 bg-slate-50 text-right text-xs font-semibold text-slate-500 uppercase tracking-wider">Acciones</th>
                              </tr>
                            </thead>
                            <tbody className="bg-white divide-y divide-slate-100">
                              {cargaLectiva.map((carga) => (
                                <tr key={carga.id} className="hover:bg-slate-50">
                                  <td className="px-4 py-3 text-sm text-slate-text font-medium">
                                    {getUDName(carga.unidad_didactica_id)}
                                  </td>
                                  <td className="px-4 py-3 text-sm text-slate-600">
                                    {carga.docente_nombre || getDocenteName(carga.docente_id)}
                                  </td>
                                  <td className="px-4 py-3 text-sm text-slate-500">
                                    <span className="badge-blue mr-2">
                                      {carga.turno}
                                    </span>
                                    <span className="badge-slate">
                                      Sec {carga.seccion}
                                    </span>
                                  </td>
                                  <td className="py-3 px-4 text-sm">
                                    <span className={`badge ${carga.estado === 'publicado' ? 'badge-green' : 'badge-amber'}`}>
                                      {carga.estado}
                                    </span>
                                  </td>
                                  <td className="py-3 px-4 text-sm text-right">
                                    <button 
                                      onClick={() => handleDeleteCarga(carga.id)} 
                                      className="text-red-400 hover:text-red-600 p-1.5 rounded-lg hover:bg-red-50 transition-colors"
                                      title="Eliminar asignación"
                                    >
                                      <Trash2 size={16} />
                                    </button>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  </div>
                </>
              )}

              {/* Horarios Tab */}
              {activeTab === 'horarios' && (
                <div className="lg:col-span-3">
                  <div className="flex space-x-2 border-b border-slate-200 mb-6">
                    <button
                      className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${horariosSubTab === 'excel' ? 'border-primary text-primary' : 'border-transparent text-slate-500 hover:text-slate-700'}`}
                      onClick={() => setHorariosSubTab('excel')}
                    >
                      Opción 1: Subir Excel
                    </button>
                    <button
                      className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${horariosSubTab === 'visual' ? 'border-primary text-primary' : 'border-transparent text-slate-500 hover:text-slate-700'}`}
                      onClick={() => setHorariosSubTab('visual')}
                    >
                      Opción 2: Constructor Visual (Beta - D.S.I)
                    </button>
                  </div>

                  {horariosSubTab === 'excel' ? (
                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                      <div className="lg:col-span-1">
                    <div className="glass-card p-6">
                      <h3 className="text-lg font-bold text-slate-700 mb-4">Subir Horario General</h3>
                      <form onSubmit={handleUploadHorario} className="space-y-4">
                        <div>
                          <label className="label">Archivo Excel (URL por MVP)</label>
                          <input 
                            type="text" 
                            className="input-field w-full" 
                            placeholder="https://docs.google.com/spreadsheets/d/..."
                            required
                            value={horarioForm.archivo_excel_url}
                            onChange={e => setHorarioForm({...horarioForm, archivo_excel_url: e.target.value})}
                          />
                        </div>
                        <div>
                          <label className="label">Observaciones</label>
                          <textarea 
                            className="input-field w-full" 
                            placeholder="Versión inicial..."
                            value={horarioForm.observaciones}
                            onChange={e => setHorarioForm({...horarioForm, observaciones: e.target.value})}
                          ></textarea>
                        </div>
                        <button 
                          type="submit" 
                          disabled={saving}
                          className={`w-full py-2.5 rounded-xl font-medium text-white transition-all ${saving ? 'bg-slate-400 cursor-not-allowed' : 'btn-primary justify-center'}`}
                        >
                          {saving ? 'Guardando...' : 'Publicar Horario'}
                        </button>
                      </form>
                    </div>
                  </div>

                  <div className="lg:col-span-2">
                    <div className="glass-card p-6">
                      <h3 className="text-lg font-bold text-slate-700 mb-4">Horarios Publicados</h3>
                      {horarios.length === 0 ? (
                        <p className="text-sm text-slate-500 italic">No hay horarios subidos para este periodo.</p>
                      ) : (
                        <div className="space-y-4">
                          {horarios.map((h) => (
                            <div key={h.id} className="flex items-center justify-between p-4 bg-white border border-slate-200 rounded-xl hover:shadow-md transition-shadow">
                              <div className="flex items-center space-x-4">
                                <div className="h-10 w-10 bg-primary-soft rounded-full flex items-center justify-center text-primary">
                                  <BarChart3 size={20} />
                                </div>
                                <div>
                                  <h4 className="text-sm font-medium text-slate-text">Horario Consolidado</h4>
                                  <p className="text-xs text-slate-500">{new Date(h.created_at).toLocaleString()}</p>
                                  {h.observaciones && (
                                    <p className="text-sm text-slate-600 mt-1 italic">"{h.observaciones}"</p>
                                  )}
                                </div>
                              </div>
                              <div className="flex items-center space-x-2">
                                <a 
                                  href={h.archivo_excel_url} 
                                  target="_blank" 
                                  rel="noopener noreferrer"
                                  className="inline-flex items-center px-4 py-2 border border-slate-300 shadow-sm text-sm font-medium rounded-lg text-slate-700 bg-white hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary"
                                >
                                  <ExternalLink size={16} className="mr-2" /> Abrir Archivo
                                </a>
                                <button 
                                  onClick={() => handleDeleteHorario(h.id)} 
                                  className="text-red-400 hover:text-red-600 p-2 rounded-lg hover:bg-red-50 transition-colors border border-transparent hover:border-red-100"
                                  title="Eliminar horario"
                                >
                                  <Trash2 size={16} />
                                </button>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ) : (
                <ScheduleBuilder 
                  cargaLectiva={cargaLectiva} 
                  getUDName={getUDName} 
                  getDocenteName={getDocenteName} 
                />
              )}
            </div>
          )}
        </div>
      )}

      {/* External Tabs */}
          {!loading && activeTab === 'revision' && (
            <CoordinatorReview 
              programId={selectedProgram}
              periodId={selectedPeriod}
              docentes={docentesHistoricos}
              cargaLectiva={cargaLectiva}
              unidadesFlat={unidadesFlat}
            />
          )}

          {!loading && activeTab === 'tutorias' && (
            <CoordinatorTutorias 
              programId={selectedProgram}
              periodId={selectedPeriod}
              docentes={docentes}
            />
          )}

          {!loading && activeTab === 'actas' && (
            <CoordinatorActas 
              programId={selectedProgram}
              periodId={selectedPeriod}
              programs={programs}
              periods={periods}
            />
          )}
        </>
      )}

      {/* Modal Institucional para Clonar Carga Lectiva */}
      {showCloneModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-sm animate-fade-in">
          <div className="bg-white rounded-2xl border border-slate-200 shadow-xl w-full max-w-md overflow-hidden animate-scale-up">
            <div className="p-5 border-b border-slate-100 flex justify-between items-center bg-slate-50/50">
              <h4 className="font-bold text-slate-800 text-sm flex items-center gap-2">
                <Copy size={16} className="text-indigo-600" />
                Clonar Carga Lectiva
              </h4>
              <button 
                onClick={() => setShowCloneModal(false)}
                className="p-1 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100"
              >
                <X size={16} />
              </button>
            </div>
            <div className="p-5 space-y-4">
              <p className="text-xs text-slate-500 leading-relaxed">
                Seleccione el periodo académico desde el cual desea copiar las asignaciones docentes para este programa:
              </p>
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1.5">
                  Periodo de Origen
                </label>
                <select 
                  className="w-full px-3 py-2 text-sm border border-slate-200 rounded-xl focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none"
                  value={cloneFromPeriod}
                  onChange={(e) => setCloneFromPeriod(e.target.value)}
                >
                  <option value="">-- Seleccione periodo origen --</option>
                  {periods
                    .filter(p => String(p.id) !== String(selectedPeriod))
                    .map(p => (
                      <option key={p.id} value={p.id}>
                        {p.nombre || p.codigo}
                      </option>
                    ))
                  }
                </select>
              </div>
            </div>
            <div className="p-4 border-t border-slate-100 bg-slate-50/60 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowCloneModal(false)}
                className="px-4 py-2 rounded-xl text-xs font-medium text-slate-600 hover:bg-slate-200/60 transition-colors"
              >
                Cancelar
              </button>
              <button
                type="button"
                disabled={!cloneFromPeriod || loading}
                onClick={handleExecuteCloneCarga}
                className="px-4 py-2 rounded-xl text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 transition-colors"
              >
                {loading ? 'Clonando...' : 'Clonar Asignaciones'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
