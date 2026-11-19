// Lectura UNICA de los datos personales de un estudiante.
//
// `estudiantes.metadata_admision` (JSON) llega en dos formatos:
//  1. Registro manual / edicion en SIGA: claves normalizadas
//     (genero, departamento, salud_procedencia.colegio_procedencia, emergencia.*...).
//  2. Ingesta desde Admision: encabezados crudos del Excel
//     ("SEXO: ", "DEPARTAMENTO:", "INSTITUCION EDUCATIVA:", "NOMBRE DEL APODERADO:"...).
// Esta funcion acepta ambos (y las columnas propias de la tabla) para que la ficha,
// la edicion y cualquier otra vista muestren siempre el mismo dato.
import { DEPARTAMENTOS, getProvincias, getDistritos } from './ubigeoPeru';

const plain = (v) => String(v ?? '')
  .normalize('NFD').replace(/[̀-ͯ]/g, '')
  .replace(/\s+/g, '').toUpperCase();

// Claves crudas del Excel de Admision, normalizadas (sin ":" final ni espacios extra).
const rawKey = (k) => String(k).replace(/:\s*$/, '').replace(/\s+/g, ' ').trim().toUpperCase();

function rawIndex(meta) {
  const idx = {};
  Object.entries(meta || {}).forEach(([k, v]) => { idx[rawKey(k)] = v; });
  return idx;
}

const clean = (v) => {
  if (v === null || v === undefined) return null;
  const s = String(v).trim();
  return s === '' || s.toLowerCase() === 'none' || s.toLowerCase() === 'null' ? null : s;
};

const first = (...vals) => {
  for (const v of vals) {
    const c = clean(v);
    if (c !== null) return c;
  }
  return null;
};

// Busca el nombre canonico en un catalogo ignorando tildes, mayusculas y espacios.
const canon = (value, catalog) => {
  const c = clean(value);
  if (!c) return null;
  return catalog.find(item => plain(item) === plain(c)) || c;
};

const GENEROS = { M: 'Masculino', MASCULINO: 'Masculino', F: 'Femenino', FEMENINO: 'Femenino', OTRO: 'Otro' };

const siNo = (v) => {
  if (v === true || v === false) return v;
  const c = clean(v);
  if (!c) return null;
  const p = plain(c);
  if (['SI', 'S', 'TRUE', '1'].includes(p)) return true;
  if (['NO', 'N', 'FALSE', '0'].includes(p)) return false;
  return null;
};

// "2008-05-06T00:00:00" | "2008-05-06" -> "2008-05-06"
const isoDate = (v) => {
  const c = clean(v);
  const m = c && c.match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : null;
};

const nacionalidad = (v) => {
  const c = clean(v);
  if (!c) return null;
  const p = plain(c);
  if (p === 'PERUANA' || p === 'PERUANO') return 'Peruana';
  return c.charAt(0).toUpperCase() + c.slice(1).toLowerCase();
};

export function readStudentProfile(student) {
  const s = student || {};
  const meta = s.metadata_admision || {};
  const raw = rawIndex(meta);
  const socio = meta.socioeconomico || {};
  const salud = meta.salud_procedencia || {};
  const ie = meta.institucion_educativa || {};
  const emergencia = meta.emergencia || {};
  const apoderado = meta.apoderado || {};
  const ubigeo = meta.ubigeo || {};

  const departamento = canon(first(meta.departamento, ubigeo.departamento, raw['DEPARTAMENTO']), DEPARTAMENTOS);
  const provincia = canon(first(meta.provincia, ubigeo.provincia, raw['PROVINCIA']), getProvincias(departamento));
  const distrito = canon(first(meta.distrito, ubigeo.distrito, raw['DISTRITO']), getDistritos(departamento, provincia));

  const generoRaw = first(meta.genero, meta.sexo, raw['SEXO']);
  const discapacidadFlag = siNo(meta.discapacidad ?? raw['DISCAPACIDAD SI/NO']);
  const tipoDisc = first(meta.tipo_discapacidad, raw['TIPO DE DISCAPACIDAD']);
  const tipoDiscapacidad = tipoDisc && !['NINGUNA', 'NINGUNO', 'NO'].includes(plain(tipoDisc)) ? tipoDisc : null;

  const trabaja = siNo(socio.trabaja ?? meta.trabaja);
  const internet = siNo(socio.tiene_internet_en_casa ?? meta.tiene_internet_en_casa);
  const ingreso = socio.ingreso_mensual ?? meta.ingreso_mensual;

  return {
    fechaNacimiento: isoDate(s.fecha_nacimiento) || isoDate(raw['FECHA NACIMIENTO']),
    celular: first(s.celular, meta.telefono_movil, raw['CELULAR']),
    email: first(s.email_personal, meta.email_personal, raw['CORREO']),
    direccion: first(s.direccion_domicilio, meta.direccion_residencia, raw['DOMICILIO']),
    departamento,
    provincia,
    distrito,
    genero: generoRaw ? (GENEROS[plain(generoRaw)] || generoRaw) : null,
    estadoCivil: first(meta.estado_civil),
    nacionalidad: nacionalidad(first(meta.nacionalidad, raw['NACIONALIDAD'])),
    lenguaMaterna: first(meta.lengua_materna, raw['LENGUA MATERNA']),
    discapacidad: discapacidadFlag === true || Boolean(tipoDiscapacidad),
    discapacidadConocida: discapacidadFlag !== null || tipoDisc !== null,
    tipoDiscapacidad,
    trabaja,
    ingresoMensual: ingreso === undefined || ingreso === null || ingreso === '' ? null : ingreso,
    tipoVivienda: first(socio.tipo_vivienda, meta.tipo_vivienda),
    tieneInternet: internet,
    colegio: first(salud.colegio_procedencia, ie.nombre, meta.colegio_procedencia, raw['INSTITUCION EDUCATIVA']),
    tipoColegio: first(meta.tipo_colegio, raw['TIPO (ESTATAL O PARTICULAR)']),
    anioEgreso: first(salud['año_egreso_colegio'], salud.anio_egreso_colegio, ie.anio_egreso,
      meta.anio_termino_secundaria, meta['año_egreso_colegio'], raw['AÑO QUE TERMINO SECUNDARIA']),
    tipoSangre: first(salud.tipo_sangre, meta.tipo_sangre),
    seguroSalud: first(salud.seguro_salud, meta.seguro_salud),
    contactoNombre: first(emergencia.contacto_nombre, apoderado.nombre, meta.contacto_emergencia_nombre,
      raw['NOMBRE DEL APODERADO']),
    contactoTelefono: first(emergencia.contacto_telefono, apoderado.celular, meta.contacto_emergencia_telefono,
      raw['CELULAR (APODERADO)']),
    padre: first(apoderado.nombre_padre, emergencia.nombre_padre, meta.nombre_padre),
    madre: first(apoderado.nombre_madre, emergencia.nombre_madre, meta.nombre_madre),
  };
}
