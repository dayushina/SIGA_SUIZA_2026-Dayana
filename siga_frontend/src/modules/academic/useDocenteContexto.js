import { useEffect, useState } from 'react';
import { apiClient } from '../../core/api/client';
import { tieneRol } from '../../core/auth/roles';

const ACADEMICO = '/api/mod-programas-estudio';
const ESTADOS_VIGENTES = new Set(['activo', 'abierto', 'matricula_abierta', 'en_curso']);
const VACIO = { programs: [], periods: [], cargas: [], error: '', errorCarga: '' };

async function leerLista(endpoint, opciones) {
  const data = await apiClient.request(endpoint, opciones);
  if (!Array.isArray(data)) throw new Error('Respuesta incompleta');
  return data;
}

export function useDocenteContexto(user) {
  const userId = user?.id;
  const programaPerfil = String(user?.programa_id ?? '');
  const isAdmin = tieneRol(user) || ['admin', 'superadmin'].includes(user?.role);
  const clave = `${userId}/${programaPerfil}/${isAdmin}`;
  const [consulta, setConsulta] = useState(null);
  const [seleccion, setSeleccion] = useState(null);
  const { programa, periodo } = seleccion?.clave === clave ? seleccion : {};
  const datos = consulta?.clave === clave ? consulta : VACIO;

  useEffect(() => {
    if (!userId) return;
    const controller = new AbortController();
    const opciones = { signal: controller.signal, cache: 'no-store' };

    const consultar = async () => {
      const rutaCarga = `${ACADEMICO}/docentes/${userId}/carga-lectiva`;
      const [programas, periodos, carga] = await Promise.allSettled([
        leerLista(`${ACADEMICO}/programas`, opciones),
        leerLista(`${ACADEMICO}/periodos`, opciones),
        isAdmin || programaPerfil
          ? leerLista(isAdmin ? rutaCarga : `${rutaCarga}?programa_id=${encodeURIComponent(programaPerfil)}`, opciones)
          : Promise.resolve([]),
      ]);
      if (controller.signal.aborted) return;

      const programs = (programas.value ?? []).filter(p => isAdmin || String(p.id) === programaPerfil);
      const cargas = (carga.value ?? []).filter(c => isAdmin || String(c.programa_id) === programaPerfil);
      setConsulta({
        clave,
        programs,
        periods: periodos.value ?? [],
        cargas,
        error: programas.status === 'rejected' || periodos.status === 'rejected'
          ? 'No se pudo cargar el programa o el calendario académico. Vuelve a abrir el Panel Docente.'
          : !isAdmin && programaPerfil && !programs.length ? 'El programa de tu perfil no figura en el catálogo académico.' : '',
        errorCarga: carga.status === 'rejected'
          ? 'No se pudo consultar tu carga lectiva. Vuelve a abrir el Panel Docente.'
          : '',
      });
    };

    consultar();
    return () => controller.abort();
  }, [clave, userId, programaPerfil, isAdmin]);

  const { programs, periods, cargas, error, errorCarga } = datos;
  const programaConCarga = programs.find(p => cargas.some(c => String(c.programa_id) === String(p.id)));
  const selectedProgram = programa ?? String((programaConCarga ?? programs[0])?.id ?? '');
  const cargasDelPrograma = cargas.filter(c => String(c.programa_id) === selectedProgram);
  const periodoVigente = (p) => ESTADOS_VIGENTES.has(p.estado?.toLowerCase());
  const tieneCarga = (p) => cargasDelPrograma.some(c => String(c.periodo_id) === String(p.id));
  const periodoInicial = periods.find(p => periodoVigente(p) && tieneCarga(p))
    ?? periods.find(tieneCarga) ?? periods.find(periodoVigente) ?? periods[0];
  const selectedPeriod = periodo ?? String(periodoInicial?.id ?? '');

  return {
    isAdmin, programs, periods, selectedProgram, selectedPeriod,
    setSelectedProgram: value => setSeleccion({ clave, programa: value, periodo }),
    setSelectedPeriod: value => setSeleccion({ clave, programa, periodo: value }),
    cargasDelPeriodo: cargasDelPrograma.filter(c => String(c.periodo_id) === selectedPeriod),
    programaAsignado: Boolean(programaPerfil), cargandoContexto: Boolean(userId && consulta?.clave !== clave),
    errorContexto: error, errorCarga,
  };
}
