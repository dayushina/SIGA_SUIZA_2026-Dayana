from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Depends
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
from sqlalchemy import delete

from database import engine, Base, get_db
from models import PostulanteAdmitido

import sys
sys.path.append("/app")

is_published = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, checkfirst=True)
    except Exception as e:
        print(f"init_db: tablas ya existen o error menor: {e}")
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
            <p style="color: #64748b;">Carga el archivo Excel de resultados de admisión (ADMISION_2026.xlsx) para procesar e inyectar a los estudiantes en el sistema.</p>
            
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
                        btn.innerText = "¡Procesado Exitosamente!";
                        statusDiv.innerText = `✅ ${data.message}`;
                        statusDiv.style.color = '#15803d';
                    } else {
                        throw new Error(data.detail || "Error al procesar el archivo");
                    }
                } catch (err) {
                    btn.disabled = false;
                    btn.innerText = "Subir y Procesar Resultados";
                    statusDiv.innerText = `❌ ${err.message}`;
                    statusDiv.style.color = '#dc2626';
                }
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


from starlette.concurrency import run_in_threadpool

from admision_excel import parse_excel, resolver_programa_id

MAX_EXCEL_BYTES = 15 * 1024 * 1024  # 15 MB


@app.post("/upload")
async def upload_excel(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """Carga el Excel de resultados (hojas B_Datos y B_Datos_Exonerados).

    Reemplaza la lista de admitidos publicada; no toca la tabla de estudiantes
    (eso lo hace la ingesta de mod-gestion-academica).
    """
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Suba el Excel de resultados en formato .xlsx.")
    contents = await file.read()
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
        await db.execute(delete(PostulanteAdmitido))
        db.add_all(PostulanteAdmitido(**f) for f in filas)
        await db.commit()
    except Exception:
        await db.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="No se pudo guardar la lista de admitidos.")

    global is_published
    is_published = True

    sin_carrera = sum(1 for f in filas if resolver_programa_id(f["programa_nombre"]) is None)
    return {
        "message": f"Se procesaron {len(filas)} postulantes. Resultados publicados.",
        "total": len(filas),
        "sin_carrera_reconocida": sin_carrera,
        "avisos": avisos[:50],
    }


@app.get("/admitidos")
async def get_admitidos(db: AsyncSession = Depends(get_db)):
    """Lista de admitidos en el formato que espera la ingesta del Core.

    `programa_id` usa el catalogo oficial (ver admision_excel.PROGRAMAS_OFICIALES);
    es null si la carrera del Excel no se reconoce.
    """
    result = await db.execute(select(PostulanteAdmitido).order_by(PostulanteAdmitido.id))
    formatted = [
        {
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
