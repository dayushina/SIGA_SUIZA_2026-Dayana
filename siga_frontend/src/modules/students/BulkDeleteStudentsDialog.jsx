import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Filter, ListChecks, Loader2, Search, Trash2, X } from 'lucide-react';
import { apiClient } from '../../core/api/client';

const LIMIT = 5000;
const MANUAL_LIMIT = 60;
const CONFIRMATION = 'ELIMINAR';
const API = '/api/mod-gestion-academica/estudiantes/lote';
const secondaryButton = 'rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50';
const primaryButton = 'inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50';
const inputClass = 'w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-900 outline-none focus-visible:ring-2 focus-visible:ring-primary/50';
const nombre = (student, id) => student
  ? `${student.nombres} ${student.apellidos}`
  : `Estudiante #${id}`;

export function BulkDeleteStudentsDialog({ students, pageStudents, scopeLabel, onClose, onDeleted }) {
  const dialogRef = useRef(null);
  const reviewRef = useRef(null);
  const pending = useRef(false);
  const [scope, setScope] = useState('manual');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(() => new Set());
  const [preview, setPreview] = useState(null);
  const [result, setResult] = useState(null);
  const [motivo, setMotivo] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const dialog = dialogRef.current;
    dialog.showModal();
    return () => dialog.close();
  }, []);

  useEffect(() => {
    if (preview || result) reviewRef.current?.focus();
  }, [preview, result]);

  const fields = query.trim().toLocaleLowerCase().split(/\s+/);
  const matches = scope === 'manual' ? students.filter(s => {
    const text = `${s.nombres} ${s.apellidos} ${s.codigo_estudiante ?? ''} ${s.dni ?? ''}`.toLocaleLowerCase();
    return fields.every(field => text.includes(field));
  }) : [];
  const ids = scope === 'manual' ? [...selected]
    : (scope === 'page' ? pageStudents : students).map(s => s.id);
  const studentById = new Map(students.map(s => [s.id, s]));
  const scopes = [
    { id: 'manual', title: 'Selección manual', detail: 'Marca estudiantes uno por uno.', count: selected.size, icon: ListChecks },
    { id: 'page', title: 'Página actual', detail: 'Incluye solo la página que estabas viendo.', count: pageStudents.length, icon: ListChecks },
    { id: 'filtered', title: 'Todos los resultados', detail: scopeLabel, count: students.length, icon: Filter },
  ];
  const omitidos = result?.omitidos ?? preview?.omitidos ?? [];
  const normalizedReason = motivo.trim().replace(/\s+/g, ' ');
  const canSubmit = preview
    ? preview.elegibles.length > 0 && normalizedReason.length >= 5 && confirmation.trim() === CONFIRMATION
    : ids.length > 0 && ids.length <= LIMIT;

  function toggleStudent(id) {
    setSelected(current => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function backToSelection() {
    setPreview(null);
    setConfirmation('');
    setError('');
    requestAnimationFrame(() => dialogRef.current?.querySelector('input:checked')?.focus());
  }

  async function submit(event) {
    event.preventDefault();
    if (pending.current || result || !canSubmit) return;

    pending.current = true;
    setBusy(true);
    setError('');
    try {
      if (!preview) {
        const data = await apiClient.request(`${API}/vista-previa`, { method: 'POST', data: { ids } });
        if (!Array.isArray(data?.elegibles) || !Array.isArray(data?.omitidos)) {
          throw new Error('No se pudo verificar la selección. Vuelve a intentarlo.');
        }
        setPreview(data);
      } else {
        const data = await apiClient.request(`${API}/eliminar`, {
          method: 'POST',
          data: { ids: preview.elegibles, motivo: normalizedReason, confirmacion: CONFIRMATION },
        });
        if (!Array.isArray(data?.eliminados) || !Array.isArray(data?.omitidos)) {
          throw new Error('No se pudo confirmar el resultado. Vuelve a revisar el lote.');
        }
        setResult({ ...data, omitidos: [...preview.omitidos, ...data.omitidos] });
        onDeleted(data.eliminados);
      }
    } catch (err) {
      if (preview) {
        setPreview(null);
        setConfirmation('');
      }
      setError(err.message);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  function close() {
    if (!pending.current) onClose();
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="bulk-delete-heading"
      onCancel={event => { event.preventDefault(); close(); }}
      className="m-auto max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-2xl overflow-hidden rounded-2xl border border-slate-200 bg-white p-0 text-slate-900 shadow-2xl backdrop:bg-slate-950/60 backdrop:backdrop-blur-sm"
    >
      <form onSubmit={submit} className="flex max-h-[inherit] flex-col">
        <header className="flex shrink-0 items-start gap-3 border-b border-slate-200 px-5 py-4 sm:px-6">
          <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-rose-50 text-rose-700">
            <Trash2 size={21} aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="bulk-delete-heading" className="text-lg font-bold tracking-tight">Eliminar estudiantes por lotes</h2>
            <p className="mt-0.5 text-sm text-slate-600">Selecciona qué registros quieres revisar para su eliminación.</p>
          </div>
          <button autoFocus type="button" onClick={close} disabled={busy} aria-label="Cerrar"
            className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900 disabled:opacity-50">
            <X size={19} />
          </button>
        </header>

        <div className="min-h-0 space-y-4 overflow-y-auto overscroll-contain px-5 py-4 sm:px-6" aria-busy={busy}>
          {error && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">{error}</p>}

          {!preview && !result && (
            <>
              <fieldset disabled={busy} className="space-y-2">
                <legend className="mb-2 text-sm font-bold">¿Qué estudiantes quieres incluir?</legend>
                {scopes.map(({ id, title, detail, count, icon: Icon }) => (
                  <label key={id} className={`flex cursor-pointer items-center gap-3 rounded-xl border px-3 py-2.5 focus-within:ring-2 focus-within:ring-primary/40 ${scope === id ? 'border-primary/40 bg-primary/5' : 'border-slate-200 hover:bg-slate-50'}`}>
                    <input type="radio" name="bulk-student-scope" checked={scope === id} onChange={() => setScope(id)} className="size-4 shrink-0 accent-primary" />
                    <Icon size={18} className="hidden shrink-0 text-primary sm:block" aria-hidden="true" />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-semibold">{title}</span>
                      <span className="block break-words text-xs text-slate-600">{detail}</span>
                    </span>
                    <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-bold tabular-nums text-slate-700">{count}</span>
                  </label>
                ))}
              </fieldset>

              {scope === 'manual' && (
                <div className="rounded-xl border border-slate-200">
                  <label className="flex items-center gap-2 border-b border-slate-200 px-3 py-2">
                    <Search size={16} className="shrink-0 text-slate-500" aria-hidden="true" />
                    <input type="search" value={query} onChange={event => setQuery(event.target.value)} disabled={busy}
                      aria-label="Buscar en la selección manual" placeholder="Buscar por nombre, DNI o código"
                      className="min-w-0 flex-1 rounded-md px-2 py-1 text-sm focus-visible:ring-2 focus-visible:ring-primary/50" />
                  </label>
                  <div className="max-h-48 divide-y divide-slate-100 overflow-auto" aria-label="Estudiantes disponibles">
                    {matches.slice(0, MANUAL_LIMIT).map(student => (
                      <label key={student.id} className="flex cursor-pointer items-center gap-3 px-4 py-2 hover:bg-slate-50">
                        <input type="checkbox" checked={selected.has(student.id)} onChange={() => toggleStudent(student.id)} disabled={busy}
                          className="size-4 shrink-0 accent-primary" />
                        <span className="min-w-0 text-sm">
                          <span className="block break-words font-medium">{nombre(student)}</span>
                          <span className="text-xs text-slate-600">{[student.codigo_estudiante, student.dni].filter(Boolean).join(' · ')}</span>
                        </span>
                      </label>
                    ))}
                    {!matches.length && <p className="px-4 py-3 text-sm text-slate-600">No hay estudiantes que coincidan con la búsqueda.</p>}
                  </div>
                  {matches.length > MANUAL_LIMIT && (
                    <p className="border-t border-slate-200 px-4 py-2 text-xs text-slate-600">Se muestran {MANUAL_LIMIT} de {matches.length}. Usa el buscador para encontrar otros registros.</p>
                  )}
                </div>
              )}
              {ids.length > LIMIT && <p role="status" className="text-sm text-rose-800">El límite por lote es de {LIMIT} estudiantes. Reduce la selección o aplica un filtro en el padrón.</p>}
            </>
          )}

          {(preview || result) && (
            <>
              <div tabIndex={-1} ref={reviewRef} className="rounded-xl border border-slate-200 bg-slate-50 p-4 outline-none">
                <h3 className="flex items-center gap-2 text-sm font-bold">
                  {result ? <CheckCircle2 size={17} className="text-emerald-700" /> : <ListChecks size={17} className="text-primary" />}
                  {result ? 'Resultado de la eliminación' : 'Revisión del lote'}
                </h3>
                <p className="mt-1 text-sm text-slate-700">
                  {result ? `Eliminados: ${result.eliminados.length}.` : `Se pueden eliminar: ${preview.elegibles.length} de ${preview.elegibles.length + omitidos.length}.`}
                  {' '}Omitidos: {omitidos.length}.
                </p>
                {!result && preview.elegibles.length > 0 && (
                  <details className="mt-2 text-sm">
                    <summary className="cursor-pointer font-semibold text-primary">Ver estudiantes que se eliminarán</summary>
                    <ul className="mt-2 max-h-36 list-disc space-y-1 overflow-auto pl-5 text-slate-700">
                      {preview.elegibles.map(id => <li key={id}>{nombre(studentById.get(id), id)}</li>)}
                    </ul>
                  </details>
                )}
              </div>
              {omitidos.length > 0 && (
                <details className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
                  <summary className="cursor-pointer font-semibold">Ver los motivos de omisión ({omitidos.length})</summary>
                  <ul className="mt-2 max-h-36 list-disc space-y-2 overflow-auto pl-5">
                    {omitidos.map(({ id, motivos }) => (
                      <li key={id}><strong>{nombre(studentById.get(id), id)}</strong><span className="block">{motivos.join('; ')}</span></li>
                    ))}
                  </ul>
                </details>
              )}
              {!result && preview.elegibles.length > 0 && (
                <>
                  <p className="flex gap-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-900">
                    <AlertTriangle size={18} className="shrink-0" aria-hidden="true" />
                    La eliminación es definitiva. Se registra el motivo en la auditoría.
                  </p>
                  <label className="block text-sm font-semibold">
                    Motivo de la eliminación
                    <textarea rows={2} value={motivo} maxLength={500} disabled={busy} onChange={event => setMotivo(event.target.value)}
                      placeholder="Por ejemplo, registros duplicados" className={`mt-1.5 ${inputClass}`} />
                  </label>
                  <label className="block text-sm font-semibold">
                    Escribe <strong className="text-rose-800">{CONFIRMATION}</strong> para confirmar
                    <input value={confirmation} disabled={busy} onChange={event => setConfirmation(event.target.value)}
                      autoComplete="off" spellCheck={false} className={`mt-1.5 ${inputClass}`} />
                  </label>
                </>
              )}
            </>
          )}
        </div>

        <footer className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-slate-200 bg-slate-50 px-5 py-3 sm:px-6">
          <p className="text-sm font-semibold text-slate-700" aria-live="polite">
            {!preview && !result ? `Seleccionados: ${ids.length}` : ''}
          </p>
          <div className="ml-auto flex flex-wrap justify-end gap-2">
            {result ? (
              <button type="button" onClick={close} className={`${primaryButton} bg-primary hover:bg-primary-dark`}>Listo</button>
            ) : (
              <>
                <button type="button" disabled={busy} onClick={preview ? backToSelection : close} className={secondaryButton}>
                  {preview ? 'Volver' : 'Cancelar'}
                </button>
                {(!preview || preview.elegibles.length > 0) && (
                  <button type="submit"
                    disabled={busy || !canSubmit}
                    className={`${primaryButton} ${preview ? 'bg-rose-700 hover:bg-rose-800' : 'bg-primary hover:bg-primary-dark'}`}>
                    {busy && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
                    {preview ? `Eliminar (${preview.elegibles.length})` : 'Revisar selección'}
                  </button>
                )}
              </>
            )}
          </div>
        </footer>
      </form>
    </dialog>
  );
}
