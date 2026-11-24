import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, ClipboardX, Loader2, Search, X } from 'lucide-react';
import { apiClient } from '../../core/api/client';
import { getPeriodLabel } from './periodUtils';

const API = '/api/mod-gestion-academica/matriculas/lote';
const CONFIRMACION = 'DESMATRICULAR';
const CONTROL = 'input-field mt-1.5 w-full';

export function BulkUnenrollmentDialog({ programas, programaInicial, busquedaInicial, onClose, onApplied }) {
  const dialog = useRef(null);
  const operando = useRef(false);
  const [periodos, setPeriodos] = useState(null);
  const [periodoId, setPeriodoId] = useState('');
  const [programaId, setProgramaId] = useState(programaInicial === 'ALL' ? '' : programaInicial);
  const [busqueda, setBusqueda] = useState(busquedaInicial);
  const [vista, setVista] = useState(null);
  const [seleccion, setSeleccion] = useState(new Set());
  const [motivo, setMotivo] = useState('');
  const [confirmacion, setConfirmacion] = useState('');
  const [resultado, setResultado] = useState(null);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const ventana = dialog.current;
    ventana.showModal();
    let activo = true;
    apiClient.request('/api/mod-programas-estudio/periodos', { cache: 'no-store' })
      .then(data => {
        if (!Array.isArray(data)) throw new Error('No se pudo consultar el catálogo de periodos.');
        if (activo) setPeriodos(data);
      })
      .catch(err => { if (activo) { setPeriodos([]); setError(err.message); } });
    return () => {
      activo = false;
      ventana.close();
    };
  }, []);

  const elegibles = vista?.filter(m => !m.bloqueo) ?? [];
  const omitidas = (vista?.filter(m => m.bloqueo) ?? []).length;
  const motivoLimpio = motivo.trim().replace(/\s+/g, ' ');
  const listo = seleccion.size > 0 && motivoLimpio.length >= 5 && confirmacion.trim() === CONFIRMACION;
  const nombres = new Map((vista ?? []).map(m => [m.id, m.nombre]));
  const periodo = periodos?.find(p => String(p.id) === periodoId);

  function cerrar() {
    if (!operando.current) onClose();
  }

  function volver() {
    setVista(null);
    setSeleccion(new Set());
    setConfirmacion('');
    setError('');
  }

  function marcar(id) {
    setSeleccion(actual => {
      const nuevos = new Set(actual);
      if (nuevos.has(id)) nuevos.delete(id);
      else nuevos.add(id);
      return nuevos;
    });
  }

  async function enviar(event) {
    event.preventDefault();
    if (operando.current || resultado || !periodo || (vista && !listo)) return;
    operando.current = true;
    setOcupado(true);
    setError('');
    try {
      if (!vista) {
        const data = await apiClient.request(`${API}/vista-previa`, {
          method: 'POST', data: { periodo_id: Number(periodoId), programa_id: Number(programaId) || null, q: busqueda.trim() },
        });
        if (!Array.isArray(data?.matriculas)) throw new Error('No se pudo verificar el periodo. Vuelve a intentarlo.');
        setVista(data.matriculas);
      } else {
        const data = await apiClient.request(`${API}/anular`, {
          method: 'POST',
          data: { periodo_id: Number(periodoId), ids: [...seleccion], motivo: motivoLimpio, confirmacion: CONFIRMACION },
        });
        if (!Array.isArray(data?.anuladas) || !Array.isArray(data?.omitidas)) {
          throw new Error('No se pudo confirmar el resultado. Vuelve a consultar el periodo.');
        }
        setResultado(data);
        onApplied();
      }
    } catch (err) {
      if (vista) volver();
      setError(err.message);
    } finally {
      operando.current = false;
      setOcupado(false);
    }
  }

  return (
    <dialog ref={dialog} aria-labelledby="titulo-desmatricula" onCancel={e => { e.preventDefault(); cerrar(); }}
      className="m-auto max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-2xl overflow-hidden rounded-2xl border border-slate-200 bg-white text-slate-900 shadow-2xl backdrop:bg-slate-950/60 backdrop:backdrop-blur-sm">
      <form onSubmit={enviar} className="flex max-h-[inherit] flex-col">
        <header className="flex shrink-0 items-start gap-3 border-b border-slate-200 px-5 py-4">
          <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-rose-50 text-rose-700"><ClipboardX size={22} aria-hidden="true" /></span>
          <div className="flex-1">
            <h2 id="titulo-desmatricula" className="text-lg font-bold">Desmatricular por lotes</h2>
            <p className="text-sm text-slate-600">Elige un periodo y revisa las matrículas que puedes anular.</p>
          </div>
          <button type="button" autoFocus onClick={cerrar} disabled={ocupado} aria-label="Cerrar" className="rounded-lg p-2 text-slate-500 hover:bg-slate-100"><X size={19} /></button>
        </header>
        <div className="min-h-0 space-y-4 overflow-y-auto overscroll-contain px-5 py-4" aria-busy={ocupado}>
          {error && <p role="alert" className="rounded-xl bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
          {!vista && (
            <fieldset disabled={ocupado} className="grid gap-3 sm:grid-cols-2">
              <label className="text-sm font-semibold">Periodo académico
                <select value={periodoId} className={CONTROL} required onChange={e => setPeriodoId(e.target.value)}>
                  <option value="">{periodos === null ? 'Cargando periodos…' : 'Selecciona un periodo'}</option>
                  {periodos?.map(p => <option key={p.id} value={p.id}>{getPeriodLabel(p)}</option>)}
                </select>
              </label>
              <label className="text-sm font-semibold">Programa de estudios
                <select value={programaId} className={CONTROL} onChange={e => setProgramaId(e.target.value)}>
                  <option value="">Todos los programas</option>
                  {programas.map(p => <option key={p.id} value={p.id}>{p.nombre}</option>)}
                </select>
              </label>
              <label className="text-sm font-semibold sm:col-span-2">Buscar estudiante
                <input type="search" value={busqueda} maxLength={150} className={CONTROL} onChange={e => setBusqueda(e.target.value)} placeholder="DNI, nombre o código" />
              </label>
            </fieldset>
          )}
          {vista && !resultado && (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3">
                <p className="text-sm font-semibold">{getPeriodLabel(periodo)} · {vista.length} matrículas · {omitidas} protegidas</p>
                {elegibles.length > 0 && <button type="button" disabled={ocupado}
                  onClick={() => setSeleccion(seleccion.size === elegibles.length ? new Set() : new Set(elegibles.map(m => m.id)))}
                  className="text-sm font-semibold text-primary underline">
                  {seleccion.size === elegibles.length ? 'Quitar selección' : 'Seleccionar todas las elegibles'}
                </button>}
              </div>
              {vista.length > 0 ? (
                <div className="max-h-52 divide-y divide-slate-100 overflow-auto rounded-xl border border-slate-200">
                  {vista.map(m => (
                    <label key={m.id} className="flex items-start gap-3 px-3 py-2 text-sm">
                      <input type="checkbox" disabled={ocupado || Boolean(m.bloqueo)} checked={seleccion.has(m.id)}
                        onChange={() => marcar(m.id)} className="mt-1 size-4 shrink-0 accent-primary" />
                      <span className="min-w-0">
                        <span className="block font-semibold">{m.nombre}</span>
                        <span className="block text-xs text-slate-600">{m.dni} · {programas.find(p => p.id === m.programa_id)?.nombre ?? `Programa #${m.programa_id}`} · Ciclo {m.ciclo ?? 'sin definir'}</span>
                        {m.bloqueo && <span className="block text-xs text-amber-800">{m.bloqueo}</span>}
                      </span>
                    </label>
                  ))}
                </div>
              ) : <p className="rounded-xl border border-slate-200 p-3 text-sm text-slate-600">No hay matrículas vigentes con esos filtros.</p>}
              {seleccion.size > 0 && (
                <fieldset disabled={ocupado} className="space-y-3">
                  <p className="flex gap-2 rounded-xl bg-rose-50 p-3 text-sm text-rose-900"><AlertTriangle size={18} className="shrink-0" aria-hidden="true" />Se anularán {seleccion.size} matrículas. Los expedientes y pagos se conservan.</p>
                  <label className="block text-sm font-semibold">Motivo de la desmatrícula
                    <textarea value={motivo} rows={2} maxLength={500} onChange={e => setMotivo(e.target.value)} className={CONTROL} placeholder="Indica por qué corresponde anularlas" />
                  </label>
                  <label className="block text-sm font-semibold">Escribe {CONFIRMACION} para confirmar
                    <input value={confirmacion} autoComplete="off" onChange={e => setConfirmacion(e.target.value)} className={CONTROL} />
                  </label>
                </fieldset>
              )}
            </>
          )}
          {resultado && (
            <div role="status" className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm">
              <p className="font-bold">Matrículas anuladas: {resultado.anuladas.length}</p>
              <p>Omitidas al confirmar: {resultado.omitidas.length}</p>
              {!!resultado.omitidas.length && <ul className="mt-2 list-disc space-y-1 pl-5">{resultado.omitidas.map(m => (
                <li key={m.id}>{nombres.get(m.id) ?? `Matrícula #${m.id}`}: {m.motivo}</li>
              ))}</ul>}
            </div>
          )}
        </div>
        <footer className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-slate-200 bg-slate-50 px-5 py-3">
          <button type="button" disabled={ocupado} className="btn-ghost" onClick={vista && !resultado ? volver : cerrar}>
            {resultado ? 'Listo' : vista ? 'Cambiar filtros' : 'Cancelar'}
          </button>
          {!resultado && <button type="submit" disabled={ocupado || !periodo || (Boolean(vista) && !listo)}
            className={vista ? 'inline-flex items-center gap-2 rounded-xl bg-rose-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-rose-800 disabled:opacity-50' : 'btn-primary inline-flex items-center gap-2 disabled:opacity-50'}>
            {ocupado ? <Loader2 size={17} className="animate-spin" aria-hidden="true" /> : !vista && <Search size={17} aria-hidden="true" />}
            {vista ? `Desmatricular (${seleccion.size})` : 'Revisar matrículas'}
          </button>}
        </footer>
      </form>
    </dialog>
  );
}
