"""Autorizacion por rol para operaciones destructivas de Gestion Academica.

El Gateway solo exige el permiso generico `mod-gestion-academica:write`, que
tienen casi todos los roles. Anular boletas, desmatricular o eliminar
estudiantes requiere ademas un rol concreto, verificado aqui contra la BD
(shared.identity valida el X-Internal-Token del Gateway y el X-User-ID).
"""
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import column, select, table, text

_APP_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from shared.identity import es_superadmin, get_identity  # noqa: E402
from shared.programas import programa_implementado  # noqa: E402

# El superadmin siempre esta autorizado.
ROLES_ANULAR_PAGO = frozenset({"caja_tesoreria"})
ROLES_DESMATRICULAR = frozenset({"secretaria_academica"})
ROLES_ELIMINAR_ESTUDIANTE = frozenset()  # solo superadmin
ROLES_INGESTA_ADMISION = frozenset({"admin"})

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


async def programa_de_secretaria(request: Request, db: AsyncSession, solicitado: int | None):
    identidad = await get_identity(request, db)
    if es_superadmin(identidad) or "secretaria_programa" not in identidad.roles:
        return None
    try:
        nombre = await programa_implementado(db, identidad.programa_id)
    except HTTPException as exc:
        raise HTTPException(409, detail=exc.detail) from exc
    if solicitado is not None and solicitado != identidad.programa_id:
        raise HTTPException(403, detail="La cuenta solo puede operar en su programa asignado.")
    return identidad.programa_id, nombre


def filtro_programa_estudiante(programa_id: int, nombre: str):
    # Los expedientes originados en Admisión pueden conservar un ID del catálogo
    # de referencia. Sin plan explícito, su denominación evita cruces de carreras.
    nombre = " ".join("".join(
        c for c in unicodedata.normalize("NFKD", nombre.upper()) if not unicodedata.combining(c)
    ).split())
    return text(r"""
        CASE WHEN estudiantes.plan_estudios_id IS NOT NULL THEN estudiantes.programa_id = :programa
        ELSE COALESCE((
            SELECT bool_and(
                regexp_replace(translate(upper(trim(j.value)), 'ÁÉÍÓÚÜÑ', 'AEIOUUN'), '\s+', ' ', 'g')
                = :nombre_programa
            )
            FROM json_each_text(
                CASE WHEN json_typeof(estudiantes.metadata_admision::json) = 'object'
                     THEN estudiantes.metadata_admision::json ELSE '{}'::json END
            ) j
            WHERE regexp_replace(upper(trim(j.key)), '\s+', ' ', 'g') = 'PROGRAMA DE ESTUDIOS'
              AND NULLIF(trim(j.value), '') IS NOT NULL
        ), estudiantes.programa_id = :programa) END
    """).bindparams(programa=programa_id, nombre_programa=nombre)


async def validar_estudiante_secretaria(request, db, estudiante_id, programa_id, *, exigir_pago=False):
    programa = await programa_de_secretaria(request, db, programa_id)
    if programa is None:
        return
    alumnos = table("estudiantes", column("id"), column("pago_matricula"))
    consulta = select(alumnos.c.id, alumnos.c.pago_matricula).where(
        alumnos.c.id == estudiante_id, filtro_programa_estudiante(*programa),
    )
    if exigir_pago:
        consulta = consulta.with_for_update()
    alumno = (await db.execute(consulta)).one_or_none()
    if alumno is None:
        raise HTTPException(404, detail="El estudiante no está en el padrón del programa asignado.")
    if exigir_pago and not alumno.pago_matricula:
        raise HTTPException(409, detail="El estudiante todavía no tiene confirmado el pago de matrícula.")


def validar_motivo(motivo) -> str:
    texto = " ".join(str(motivo or "").split())
    if len(texto) < MOTIVO_MIN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"Indique el motivo (mínimo {MOTIVO_MIN} caracteres).")
    return texto[:500]
