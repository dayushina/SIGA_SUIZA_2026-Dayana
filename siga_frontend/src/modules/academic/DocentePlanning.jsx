/* eslint-disable react-hooks/set-state-in-effect */
import { useState, useEffect, useRef } from 'react';
import { useAuth } from '../../core/auth/useAuth';
import { apiClient } from '../../core/api/client';
import { useToast } from '../../core/ui/ToastContext';
import { useDocenteContexto } from './useDocenteContexto';
import { GraduationCap, Calendar, ClipboardList, BookOpen, FileText, Upload, ExternalLink, CheckCircle2, RefreshCw } from 'lucide-react';

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

export function DocentePlanning() {
  const { user } = useAuth();
  const {
    isAdmin, programs, periods, selectedProgram, setSelectedProgram, selectedPeriod, setSelectedPeriod,
    cargasDelPeriodo: cargaActiva, programaAsignado, cargandoContexto, errorContexto, errorCarga,
  } = useDocenteContexto(user);
  const { toast } = useSafeToast();
  const [loading, setLoading] = useState(false);
  const [planActual, setPlanActual] = useState(null);
  const [mallaFlat, setMallaFlat] = useState([]);
  const [silabos, setSilabos] = useState([]);

  const requiereMalla = cargaActiva.some(c => !c.unidad_nombre);
  useEffect(() => {
    setMallaFlat([]);
    if (!selectedProgram || !requiereMalla) return;
    const controller = new AbortController();
    apiClient.request(`/api/mod-programas-estudio/programas/${selectedProgram}/malla`, { signal: controller.signal })
      .then(res => {
        if (!controller.signal.aborted && Array.isArray(res)) setMallaFlat(res.flatMap(modulo => modulo.unidades ?? []));
      })
      .catch(error => {
        if (!controller.signal.aborted) console.error('Error fetching malla', error);
      });
    return () => controller.abort();
  }, [selectedProgram, requiereMalla]);

  useEffect(() => {
    setPlanActual(null);
    setSilabos([]);
    if (!selectedProgram || !selectedPeriod || !user?.id) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    const opciones = { signal: controller.signal, cache: 'no-store' };
    setLoading(true);
    Promise.allSettled([
      apiClient.request(`/api/mod-programas-estudio/docente/${user.id}/planes-trabajo?periodo_id=${selectedPeriod}&programa_id=${selectedProgram}`, opciones),
      apiClient.request(`/api/mod-programas-estudio/docente/${user.id}/silabos`, opciones),
    ]).then(([planes, documentos]) => {
      if (controller.signal.aborted) return;
      if (Array.isArray(planes.value)) setPlanActual(planes.value.find(p => String(p.programa_id) === selectedProgram) ?? null);
      if (Array.isArray(documentos.value)) setSilabos(documentos.value);
      setLoading(false);
    });
    return () => controller.abort();
  }, [selectedProgram, selectedPeriod, user?.id]);

  const fileInputRefSilabo = useRef(null);
  const fileInputRefPlan = useRef(null);
  const uploadingTargetRef = useRef(null);
  const [uploadingTarget, setUploadingTarget] = useState(null); // { type: 'silabo' | 'plan', id?: number }

  const triggerUploadSilabo = (cargaId) => {
    const target = { type: 'silabo', id: cargaId };
    uploadingTargetRef.current = target;
    setUploadingTarget(target);
    if (fileInputRefSilabo.current) fileInputRefSilabo.current.click();
  };

  const handleUploadSilabo = async (e) => {
    const file = e.target.files && e.target.files[0];
    const target = uploadingTargetRef.current || uploadingTarget;
    if (!file) {
      uploadingTargetRef.current = null;
      setUploadingTarget(null);
      return;
    }
    if (!target || target.type !== 'silabo') return;
    if (!user?.id) {
      toast.error("No se pudo identificar la sesión del docente.");
      return;
    }

    // Simular subida (en producción se subiría a S3/GCS y se obtendría la URL)
    const simulatedUrl = `/docs/silabos/${file.name.replace(/\s+/g, '_')}`;
    const cargaId = target.id;

    try {
      const payload = {
        carga_lectiva_id: parseInt(cargaId, 10),
        estado: "presentado",
        archivo_url: simulatedUrl
      };
      await apiClient.request('/api/mod-programas-estudio/silabos', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      toast.success("Sílabo enviado correctamente al Coordinador");

      // Refresh silabos con tolerancia a fallos
      const silabosRes = await apiClient.request(`/api/mod-programas-estudio/docente/${user.id}/silabos`).catch(() => []);
      if (silabosRes) {
        setSilabos(silabosRes);
      }
    } catch (err) {
      toast.error(err.message || "Error al enviar el sílabo");
    } finally {
      e.target.value = ''; // reset input
      uploadingTargetRef.current = null;
      setUploadingTarget(null);
    }
  };

  const triggerUploadPlan = () => {
    if (!selectedPeriod || !selectedProgram) {
      toast.warning("Por favor seleccione un programa y periodo académico antes de enviar el Plan de Trabajo.");
      return;
    }
    const target = { type: 'plan' };
    uploadingTargetRef.current = target;
    setUploadingTarget(target);
    if (fileInputRefPlan.current) fileInputRefPlan.current.click();
  };

  const handleUploadPlan = async (e) => {
    const file = e.target.files && e.target.files[0];
    const target = uploadingTargetRef.current || uploadingTarget;
    if (!file) {
      uploadingTargetRef.current = null;
      setUploadingTarget(null);
      return;
    }
    if (!target || target.type !== 'plan') return;

    if (!selectedPeriod || !selectedProgram) {
      toast.warning("Por favor seleccione un programa y periodo académico antes de enviar el Plan de Trabajo.");
      return;
    }
    if (!user?.id) {
      toast.error("No se pudo identificar la sesión del docente.");
      return;
    }

    // Simular subida
    const simulatedUrl = `/docs/planes/${file.name.replace(/\s+/g, '_')}`;

    try {
      const payload = {
        periodo_id: parseInt(selectedPeriod, 10),
        programa_id: parseInt(selectedProgram, 10),
        docente_id: parseInt(user.id, 10),
        estado: "presentado",
        archivo_url: simulatedUrl
      };
      await apiClient.request('/api/mod-programas-estudio/planes-trabajo', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      toast.success("Plan de Trabajo enviado correctamente al Coordinador");

      // Refresh planes con tolerancia a fallos
      const planesRes = await apiClient.request(
        `/api/mod-programas-estudio/docente/${user.id}/planes-trabajo?periodo_id=${selectedPeriod}&programa_id=${selectedProgram}`,
        { cache: 'no-store' },
      ).catch(() => []);
      if (Array.isArray(planesRes)) {
        setPlanActual(planesRes.find(p => String(p.programa_id) === selectedProgram) ?? null);
      }
    } catch (err) {
      toast.error(err.message || "Error al enviar el Plan de Trabajo");
    } finally {
      e.target.value = '';
      uploadingTargetRef.current = null;
      setUploadingTarget(null);
    }
  };

  const getUnidadNombre = (carga) => {
    if (carga?.unidad_nombre) return carga.unidad_nombre;
    const udId = typeof carga === 'object' ? carga?.unidad_didactica_id : carga;
    const ud = mallaFlat.find(u => u.id === udId);
    return ud ? ud.nombre : `Unidad #${udId}`;
  };

  return (
    <div className="space-y-6">
      {/* Cabecera Informativa y Filtros del Docente */}
      <div className="glass-card p-4 grid grid-cols-1 md:grid-cols-2 gap-4 border-l-4 border-l-primary mb-6 bg-gradient-to-r from-white to-slate-50">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 rounded-full bg-primary-soft flex items-center justify-center text-primary flex-shrink-0">
            <GraduationCap className="w-5 h-5" />
          </div>
          <div className="flex-1">
            <p className="text-[10px] uppercase font-extrabold text-slate-400 tracking-wider mb-1">Programa de Estudios</p>
            {isAdmin ? <select
              className="w-full px-3 py-1.5 bg-white border border-slate-200 rounded-lg text-xs font-bold text-slate-800 focus:outline-none focus:ring-1 focus:ring-primary"
              value={selectedProgram}
              onChange={(e) => setSelectedProgram(e.target.value)}
            >
              <option value="">-- Seleccione Programa --</option>
              {programs.map((p) => (
                <option key={p.id} value={p.id}>{p.nombre}</option>
              ))}
            </select> : (
              <p className="text-xs font-bold text-slate-800">
                {programs[0]?.nombre || (cargandoContexto ? 'Cargando…' : programaAsignado ? 'Programa no disponible' : 'Sin programa asignado')}
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 rounded-full bg-primary-soft flex items-center justify-center text-primary flex-shrink-0">
            <Calendar className="w-5 h-5" />
          </div>
          <div className="flex-1">
            <p className="text-[10px] uppercase font-extrabold text-slate-400 tracking-wider mb-1">Periodo Activo</p>
            <select
              className="w-full px-3 py-1.5 bg-white border border-slate-200 rounded-lg text-xs font-bold text-slate-800 focus:outline-none focus:ring-1 focus:ring-primary font-mono"
              value={selectedPeriod}
              onChange={(e) => setSelectedPeriod(e.target.value)}
            >
              <option value="">-- Seleccione Periodo --</option>
              {periods.map((p) => (
                <option key={p.id} value={p.id}>{p.codigo} {p.estado ? `— ${p.estado.replace(/_/g, ' ')}` : ''}</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {(errorContexto || errorCarga) && (
        <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">
          {errorContexto || errorCarga}
        </p>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Plan de Trabajo Global */}
          <div className="lg:col-span-1">
            <div className="glass-card p-6 h-full">
              <div className="flex items-center space-x-3 mb-6">
                <div className="w-10 h-10 rounded-xl bg-primary-soft flex items-center justify-center text-primary">
                  <ClipboardList className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-lg font-bold text-slate-text">Plan de Trabajo</h3>
                  <p className="text-xs text-slate-500">Documento global del periodo</p>
                </div>
              </div>

              {planActual ? (
                <div className="bg-green-50/50 border border-green-100 rounded-xl p-4">
                  <div className="flex justify-between items-center mb-3">
                    <span className="text-sm font-bold text-green-700">Estado: {planActual.estado.toUpperCase()}</span>
                    <span className="bg-green-100 text-green-600 text-xs px-2 py-1 rounded-full flex items-center"><CheckCircle2 className="w-3 h-3 mr-1" /> Entregado</span>
                  </div>
                  <a 
                    href={planActual.archivo_url} 
                    target="_blank" 
                    rel="noopener noreferrer"
                    className="flex items-center space-x-2 text-sm text-primary hover:text-primary-dark font-medium mb-3"
                  >
                    <ExternalLink className="w-4 h-4" />
                    <span>Ver Documento</span>
                  </a>
                  {planActual.observaciones && (
                    <div className="mt-3 bg-white p-3 rounded-lg border border-red-100">
                      <p className="text-xs font-bold text-red-600 mb-1">Observaciones del Coordinador:</p>
                      <p className="text-xs text-slate-600">{planActual.observaciones}</p>
                    </div>
                  )}
                  {planActual.estado !== 'aprobado' && (
                    <button 
                      onClick={triggerUploadPlan}
                      className="w-full mt-4 px-4 py-2 border border-slate-200 rounded-lg text-sm font-medium text-slate-600 hover:bg-slate-50 transition-colors"
                    >
                      Actualizar Documento
                    </button>
                  )}
                </div>
              ) : (
                <div className="text-center p-6 bg-slate-50/50 rounded-xl border border-slate-100 border-dashed">
                  <div className="w-12 h-12 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-3 text-primary"><FileText className="w-6 h-6" /></div>
                  <p className="text-sm text-slate-500 mb-4">Aún no has presentado tu plan de trabajo para este periodo.</p>
                  <button 
                    onClick={triggerUploadPlan}
                    disabled={!selectedProgram || !selectedPeriod}
                    className="btn-primary w-full py-2 rounded-lg text-sm disabled:opacity-50"
                  >
                    <Upload className="w-4 h-4 mr-1 inline" /> Subir Plan de Trabajo
                  </button>
                </div>
              )}
            </div>

            {/* Hidden File Inputs */}
            <input type="file" ref={fileInputRefSilabo} onChange={handleUploadSilabo} className="hidden" accept=".pdf,.doc,.docx,.xls,.xlsx" />
            <input type="file" ref={fileInputRefPlan} onChange={handleUploadPlan} className="hidden" accept=".pdf,.doc,.docx,.xls,.xlsx" />

          </div>

          {/* Carga Lectiva y Sílabos */}
          <div className="lg:col-span-2">
            <div className="glass-card p-6 h-full">
              <div className="flex items-center space-x-3 mb-6">
                <div className="w-10 h-10 rounded-xl bg-primary-soft flex items-center justify-center text-primary">
                  <BookOpen className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-lg font-bold text-slate-text">Unidades Didácticas y Sílabos</h3>
                  <p className="text-xs text-slate-500">Carga lectiva asignada por el coordinador</p>
                </div>
              </div>

              {loading || cargandoContexto ? (
                <div className="text-center py-10">
                  <div className="w-8 h-8 border-4 border-primary border-t-transparent rounded-full animate-spin mx-auto"></div>
                </div>
              ) : errorContexto || errorCarga || !selectedProgram || !selectedPeriod ? null : cargaActiva.length === 0 ? (
                <div className="text-center p-8 bg-slate-50 rounded-xl border border-slate-100">
                  <p className="text-slate-500">No tienes carga lectiva asignada para este periodo en este programa.</p>
                </div>
              ) : (
                <div className="space-y-4">
                  {cargaActiva.map(carga => {
                    const udNombre = getUnidadNombre(carga);
                    const silabo = silabos.find(s => s.carga_lectiva_id === carga.id);

                    return (
                      <div key={carga.id} className="border border-slate-100 rounded-xl p-4 hover:shadow-md transition-shadow bg-white">
                        <div className="flex flex-col md:flex-row justify-between md:items-center">
                          <div className="mb-4 md:mb-0">
                            <h4 className="font-bold text-slate-text text-md">{udNombre}</h4>
                            <div className="flex items-center space-x-3 mt-1">
                              <span className="text-xs font-medium text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full">
                                Turno: {carga.turno}
                              </span>
                              <span className="text-xs font-medium text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full">
                                Sección: {carga.seccion}
                              </span>
                            </div>
                          </div>

                          <div className="flex items-center">
                            {silabo ? (
                              <div className="text-right">
                                <div className="flex items-center space-x-2 mb-1 justify-end">
                                  <span className={`text-xs px-2 py-1 rounded-full font-medium ${
                                    silabo.estado === 'aprobado' ? 'bg-green-100 text-green-700' :
                                    silabo.estado === 'observado' ? 'bg-red-100 text-red-700' :
                                    'bg-blue-100 text-blue-700'
                                  }`}>
                                    {silabo.estado.toUpperCase()}
                                  </span>
                                </div>
                                <div className="flex space-x-2 items-center">
                                  <a href={silabo.archivo_url} target="_blank" rel="noopener noreferrer" className="text-xs text-primary hover:underline flex items-center"><ExternalLink className="w-3 h-3 mr-1" />Ver Doc</a>
                                  {silabo.estado !== 'aprobado' && (
                                    <button onClick={() => triggerUploadSilabo(carga.id)} className="text-xs text-slate-500 hover:text-slate-700 underline flex items-center">
                                      <RefreshCw className="w-3 h-3 mr-1" /> Actualizar
                                    </button>
                                  )}
                                </div>
                                {silabo.observaciones && (
                                  <p className="text-[10px] text-red-500 mt-1 max-w-[200px] truncate" title={silabo.observaciones}>
                                    Obs: {silabo.observaciones}
                                  </p>
                                )}
                              </div>
                            ) : (
                              <button 
                                onClick={() => triggerUploadSilabo(carga.id)}
                                className="flex items-center text-xs font-bold text-primary bg-primary-soft hover:bg-primary/20 px-3 py-1.5 rounded-lg transition-colors border border-primary/20"
                              >
                                <Upload className="w-4 h-4 mr-1" />
                                Subir Sílabo
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
    </div>
  );
}
