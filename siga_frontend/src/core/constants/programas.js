// Catalogo UNICO de programas de estudio para `estudiantes.programa_id`.
//
// Los ids DEBEN coincidir con el mapeo que usa Admision al crear estudiantes
// (siga_backend/modules/mod-admision/main.py -> program_mapping), que es lo que
// esta guardado en la BD. No cambiar un id sin migrar los datos.
// Nota: Asistencia Administrativa se registra con el id 4 (Gestion Administrativa).
export const PROGRAMAS_ESTUDIANTE = {
  1: 'Desarrollo de Sistemas de Información',
  2: 'Enfermería Técnica',
  4: 'Gestión Administrativa',
  5: 'Contabilidad',
  6: 'Mecatrónica Automotriz',
  7: 'Electricidad Industrial',
  8: 'Administración de Operaciones Turísticas',
  9: 'Construcción Civil',
  10: 'Producción Agropecuaria',
  11: 'Manejo Forestal',
};

// Lista ordenada alfabeticamente para selectores y filtros.
export const PROGRAMAS_ESTUDIANTE_LISTA = Object.entries(PROGRAMAS_ESTUDIANTE)
  .map(([id, label]) => ({ id, label }))
  .sort((a, b) => a.label.localeCompare(b.label, 'es'));
