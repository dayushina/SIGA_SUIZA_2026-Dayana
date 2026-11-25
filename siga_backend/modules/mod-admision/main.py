from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import uvicorn
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import column, delete, func, table, text

from database import engine, Base, get_db
from models import PostulanteAdmitido

import sys
from pathlib import Path

backend_dir = str(Path(__file__).resolve().parents[2])
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from shared.identity import es_superadmin, get_identity

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
        # Retira filas confirmadas por versiones anteriores, incluso si el
        # expediente ya se dio de baja. Las cargas nuevas nunca guardan esa marca.
        await conn.execute(delete(PostulanteAdmitido).where(PostulanteAdmitido.sincronizado.is_(True)))
        if await conn.scalar(text("SELECT to_regclass('public.estudiantes')")):
            await conn.execute(delete(PostulanteAdmitido).where(_registrado_en_padron()))
    yield

app = FastAPI(title="Módulo de Admisión", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    return {"status": "ok", "module": "mod-admision"}

@app.get("/", response_class=HTMLResponse)
async def root():
    return (
        '<html lang="es"><meta charset="utf-8"><title>Admisión</title>'
        '<h1>Módulo de Admisión</h1>'
        '<p>Publica los resultados en Admisión desde tu sesión de SIGA.</p></html>'
    )


from typing import List

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from admision_excel import parse_excel, resolver_programa_id

MAX_EXCEL_BYTES = 15 * 1024 * 1024  # 15 MB
_PADRON = table("estudiantes", column("dni"), schema="public")


def _registrado_en_padron():
    # Admisión y Gestión Académica comparten PostgreSQL.
    return (select(1).select_from(_PADRON).where(_PADRON.c.dni == PostulanteAdmitido.dni)
            .correlate(PostulanteAdmitido.__table__).exists())


@app.post("/publicaciones/excel")
async def publicar_excel(request: Request, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """Carga el Excel de resultados (hojas B_Datos y B_Datos_Exonerados).

    Reemplaza la lista de admitidos publicada; no toca la tabla de estudiantes
    (eso lo hace la ingesta de mod-gestion-academica).
    """
    identidad = await get_identity(request, db)
    if not es_superadmin(identidad) and not {"admin", "admin_admision"}.intersection(identidad.roles):
        raise HTTPException(status_code=403, detail="No tiene permisos para publicar resultados de Admisión.")
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Suba el Excel de resultados en formato .xlsx.")
    contents = await file.read(MAX_EXCEL_BYTES + 1)
    if not contents:
        raise HTTPException(status_code=400, detail="El archivo esta vacio.")
    if len(contents) > MAX_EXCEL_BYTES:
        raise HTTPException(status_code=413, detail="El archivo supera los 15 MB.")

    try:
        filas, avisos = await run_in_threadpool(parse_excel, contents)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not filas:
        raise HTTPException(status_code=400, detail="El Excel no contiene postulantes con DNI.")

    try:
        # Serializa publicaciones concurrentes sin bloquear la lectura del padrón.
        await db.execute(text("LOCK TABLE admision_postulantes IN SHARE ROW EXCLUSIVE MODE"))
        await db.execute(delete(PostulanteAdmitido))
        db.add_all(PostulanteAdmitido(**fila) for fila in filas)
        await db.flush()
        retirados = await db.execute(delete(PostulanteAdmitido).where(_registrado_en_padron()))
        ya_registrados = retirados.rowcount
        pendientes = len(filas) - ya_registrados
        await db.commit()
    except Exception:
        await db.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="No se pudo guardar la lista de admitidos.")

    sin_carrera = sum(1 for f in filas if resolver_programa_id(f["programa_nombre"]) is None)
    mensaje = f"Se procesaron {len(filas)} ingresantes. "
    if pendientes:
        mensaje += (f"{pendientes} pendientes de registrar. "
                    "Usa «Sincronizar desde Admisión» en Registro de Estudiantes.")
    else:
        mensaje += "Todos tienen expediente en el padrón. La bandeja de Admisión quedó vacía."
    return {
        "message": mensaje,
        "total": len(filas),
        "pendientes": pendientes,
        "ya_registrados": ya_registrados,
        "sin_carrera_reconocida": sin_carrera,
        "avisos": avisos[:50],
    }


@app.get("/resumen")
async def resumen_admitidos(response: Response, db: AsyncSession = Depends(get_db)):
    """La lista publicada contiene únicamente filas que quedan por procesar."""
    response.headers["Cache-Control"] = "no-store"
    pendientes = await db.scalar(select(func.count()).select_from(PostulanteAdmitido))
    return {"total": pendientes, "pendientes": pendientes, "sincronizados": 0}


class MarcarSincronizados(BaseModel):
    dnis: List[str] = Field(default_factory=list, max_length=20000)


@app.post("/admitidos/sincronizados")
async def marcar_sincronizados(data: MarcarSincronizados, db: AsyncSession = Depends(get_db)):
    """Retira filas confirmadas por clientes anteriores."""
    dnis = {str(d).strip() for d in data.dnis if str(d).strip()}
    if not dnis:
        return {"marcados": 0}
    result = await db.execute(
        delete(PostulanteAdmitido)
        .where(PostulanteAdmitido.dni.in_(dnis), _registrado_en_padron())
    )
    await db.commit()
    return {"marcados": result.rowcount or 0}


@app.get("/admitidos")
async def get_admitidos(response: Response, solo_pendientes: bool = False, db: AsyncSession = Depends(get_db)):
    """Lista de admitidos en el formato que espera la ingesta del Core.

    `programa_id` usa el catalogo oficial (ver admision_excel.PROGRAMAS_OFICIALES);
    es null si la carrera del Excel no se reconoce.
    Se acepta `solo_pendientes` por compatibilidad. Ambas consultas muestran la bandeja temporal.
    """
    response.headers["Cache-Control"] = "no-store"
    query = select(PostulanteAdmitido).order_by(PostulanteAdmitido.id)
    result = await db.execute(query)
    formatted = [
        {
            "admision_id": r.id,
            "dni": r.dni,
            "nombres": r.nombres,
            "apellidos": r.apellidos,
            "programa_id": resolver_programa_id(r.programa_nombre),
            "programa_nombre": r.programa_nombre,
            "celular": r.celular,
            "email_personal": r.email,
            "fecha_nacimiento": r.fecha_nacimiento,
            "modalidad": r.modalidad_admision,
            "metadata_completa": r.metadata_admision,
        }
        for r in result.scalars().all()
    ]
    return {"admitidos": formatted}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8009)
