// Roles del usuario (strings u objetos {name}/{nombre}) en minusculas.
export const rolesDe = (user) => (user?.roles || [])
  .map(r => (typeof r === 'string' ? r : r?.name || r?.nombre || ''))
  .map(r => String(r).toLowerCase().trim())
  .filter(Boolean);

// El superadmin siempre puede. El backend vuelve a validar: esto solo decide que botones se muestran.
export const tieneRol = (user, ...roles) => {
  if (!user) return false;
  const propios = rolesDe(user);
  return Boolean(user.is_superuser) || propios.includes('superadmin') || roles.some(r => propios.includes(r));
};

// Mismas reglas que siga_backend/modules/mod-gestion-academica/autorizacion.py
export const puedeAnularPagos = (user) => tieneRol(user, 'caja_tesoreria');
export const puedeDesmatricular = (user) => tieneRol(user, 'secretaria_academica');
export const puedeEliminarEstudiantes = (user) => tieneRol(user);
