from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import json
import os
import uvicorn
from contextlib import asynccontextmanager
from io import BytesIO
import pandas as pd
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import column, delete, func, table, text

from database import engine, Base, get_db
from models import PostulanteAdmitido

import sys
sys.path.append("/app")

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

@app.get("/")
async def root():
    html_content = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>Panel de Control - Admisión</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; }
            .card { background: white; padding: 2.5rem; border-radius: 12px; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); text-align: center; max-width: 550px; }
            .btn { background-color: #4f46e5; color: white; border: none; padding: 12px 24px; border-radius: 8px; font-weight: bold; font-size: 16px; cursor: pointer; transition: background-color 0.2s; margin-top: 15px;}
            .btn:hover { background-color: #4338ca; }
            .btn:disabled { background-color: #94a3b8; cursor: not-allowed; }
            .status { margin-top: 20px; font-weight: 500; color: #15803d; }
            .file-input-container { margin: 20px 0; padding: 20px; border: 2px dashed #cbd5e1; border-radius: 8px; background: #f1f5f9;}
        </style>
    </head>
    <body>
        <div class="card">
            <h1 style="color: #1e293b; margin-top:0;">Oficina de Admisión</h1>
            <p style="color: #64748b;">Carga el Excel de resultados para preparar la bandeja temporal de Admisión. Cada fila se retira cuando tiene un expediente registrado en el padrón.</p>
            
            <form id="uploadForm">
                <div class="file-input-container">
                    <input type="file" id="excelFile" accept=".xlsx" required />
                </div>
                <button type="submit" id="pubBtn" class="btn">
                    Subir y Procesar Resultados
                </button>
            </form>
            <div id="status" class="status"></div>
        </div>
        <script>
            document.getElementById('uploadForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                const btn = document.getElementById('pubBtn');
                const statusDiv = document.getElementById('status');
                const fileInput = document.getElementById('excelFile');
                
                if (!fileInput.files.length) return;
                
                btn.disabled = true;
                btn.innerText = "Procesando Excel...";
                statusDiv.innerText = "";
                
                const formData = new FormData();
                formData.append("file", fileInput.files[0]);
                
                try {
                    // Ruta relativa: esta pagina se sirve en la raiz del modulo (:8009/),
                    // donde el endpoint es /upload (no /api/mod-admision/upload).
                    const res = await fetch('upload', {
                        method: 'POST',
                        body: formData
                    });
                    
                    const data = await res.json();
                    if(res.ok) {
                        fileInput.value = "";
                        statusDiv.innerText = `✅ ${data.message}`;
                        statusDiv.style.color = '#15803d';
                    } else {
                        throw new Error(data.detail || "Error al procesar el archivo");
                    }
                } catch (err) {
                    statusDiv.innerText = `❌ ${err.message}`;
                    statusDiv.style.color = '#dc2626';
                } finally {
                    btn.disabled = false;
                    btn.innerText = "Subir y Procesar Resultados";
                }
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


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


@app.post("/upload")
async def upload_excel(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """Carga el Excel de resultados (hojas B_Datos y B_Datos_Exonerados).

    Reemplaza la lista de admitidos publicada; no toca la tabla de estudiantes
    (eso lo hace la ingesta de mod-gestion-academica).
    """
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
        pendientes = [PostulanteAdmitido(**fila) for fila in filas]
        db.add_all(pendientes)
        await db.flush()
        retirados = set((await db.scalars(
            delete(PostulanteAdmitido).where(_registrado_en_padron()).returning(PostulanteAdmitido.id)
        )).all())
        ids_pendientes = [fila.id for fila in pendientes if fila.id not in retirados]
        await db.commit()
    except Exception:
        await db.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="No se pudo guardar la lista de admitidos.")

    sin_carrera = sum(1 for f in filas if resolver_programa_id(f["programa_nombre"]) is None)
    mensaje = f"Se procesaron {len(filas)} ingresantes. "
    if ids_pendientes:
        mensaje += (f"{len(ids_pendientes)} pendientes de registrar. "
                    "Usa «Sincronizar desde Admisión» en Registro de Estudiantes.")
    else:
        mensaje += "Todos tienen expediente en el padrón. La bandeja de Admisión quedó vacía."
    return {
        "message": mensaje,
        "total": len(filas),
        "ids_pendientes": ids_pendientes,
        "ya_registrados": len(retirados),
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
