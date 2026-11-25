"""Validación del programa habilitado para asignaciones académicas."""

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def programa_implementado(db: AsyncSession, programa_id: int | None) -> str:
    if programa_id is None:
        raise HTTPException(422, detail="Asigna un programa de estudios a esta cuenta.")
    nombre = await db.scalar(text("""
        SELECT p.nombre FROM programas_estudio p
        WHERE p.id = :id AND p.estado = 'activo' AND p.plan_estudio_id IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM programa_modulos m
              JOIN programa_unidades u ON u.programa_modulo_id = m.id
              WHERE m.programa_id = p.id
          )
    """), {"id": programa_id})
    if not nombre:
        raise HTTPException(422, detail="El programa asignado debe estar activo y contar con una malla implementada.")
    return nombre
