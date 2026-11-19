"""Autorizacion por rol para operaciones destructivas de Gestion Academica.

El Gateway solo exige el permiso generico `mod-gestion-academica:write`, que
tienen casi todos los roles. Anular boletas, desmatricular o eliminar
estudiantes requiere ademas un rol concreto, verificado aqui contra la BD
(shared.identity valida el X-Internal-Token del Gateway y el X-User-ID).
"""
import sys
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

_APP_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from shared.identity import es_superadmin, get_identity  # noqa: E402

# El superadmin siempre esta autorizado.
ROLES_ANULAR_PAGO = frozenset({"caja_tesoreria"})
ROLES_DESMATRICULAR = frozenset({"secretaria_academica"})
ROLES_ELIMINAR_ESTUDIANTE = frozenset()  # solo superadmin

MOTIVO_MIN = 5


@dataclass
class Actor:
    user_id: int
    etiqueta: str  # correo (o id) que queda registrado en la auditoria


async def exigir_rol(request: Request, db: AsyncSession, roles: frozenset, accion: str) -> Actor:
    ident = await get_identity(request, db)
    if not (es_superadmin(ident) or roles.intersection(ident.roles)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"No tiene permisos para {accion}.")
    email = request.headers.get("X-User-Email")
    return Actor(user_id=ident.user_id, etiqueta=(email or f"usuario #{ident.user_id}")[:150])


def validar_motivo(motivo) -> str:
    texto = " ".join(str(motivo or "").split())
    if len(texto) < MOTIVO_MIN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"Indique el motivo (mínimo {MOTIVO_MIN} caracteres).")
    return texto[:500]
