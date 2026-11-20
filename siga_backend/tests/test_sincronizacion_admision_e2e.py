"""Flujo completo del boton "Sincronizar desde Admision" con la plantilla real.

Replica lo que hace el frontend (StudentMaster.procesarExcelAdmision):
  1. POST mod-admision /upload        (multipart con ADMISION_2026.xlsx)
  2. GET  mod-admision /admitidos
  3. POST mod-gestion-academica /admision/ingesta
Todo contra siga_test.
"""
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tests.helpers.module_loader import load_module_component
from tests.test_ingesta_admision import SUPERADMIN, datos, h  # noqa: F401 (fixture)

PLANTILLA = Path(__file__).resolve().parents[2] / "ADMISION_2026.xlsx"


@pytest.mark.asyncio
async def test_sincronizacion_completa_con_plantilla_real(datos):  # noqa: F811
    if not PLANTILLA.exists():
        pytest.skip("ADMISION_2026.xlsx no esta en la raiz del proyecto")

    adm_main = load_module_component("mod-admision", "main")
    adm_db = __import__("database")
    async with adm_db.engine.begin() as conn:
        await conn.run_sync(adm_db.Base.metadata.create_all, checkfirst=True)
    await datos.execute(text("DELETE FROM admision_postulantes"))
    await datos.commit()

    routes = load_module_component("mod-gestion-academica", "routes")
    gestion = FastAPI()
    gestion.include_router(routes.router)

    try:
        async with AsyncClient(transport=ASGITransport(app=adm_main.app), base_url="http://adm") as adm, \
                   AsyncClient(transport=ASGITransport(app=gestion), base_url="http://ga") as ga:
            # 1. Carga del Excel
            with PLANTILLA.open("rb") as f:
                r = await adm.post("/upload", files={"file": ("ADMISION_2026.xlsx", f,
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
            assert r.status_code == 200, r.text
            total = r.json()["total"]
            assert total > 700
            assert r.json()["sin_carrera_reconocida"] == 0

            # 2. Lista normalizada
            r = await adm.get("/admitidos")
            assert r.status_code == 200
            admitidos = r.json()["admitidos"]
            assert len(admitidos) == total
            assert all(a["programa_id"] for a in admitidos)

            # 3. Ingesta masiva (el fixture solo tiene programas_estudio id 2)
            r = await ga.post("/admision/ingesta", json={"admitidos": admitidos}, headers=h(SUPERADMIN))
            assert r.status_code == 202, r.text
            body = r.json()
            assert body["sin_programa"] == []
            assert body["con_error"] == []
            assert body["estudiantes_creados"] + body["ya_existentes"] == total
            assert 1 in body["programas_sin_plan"]  # Sistemas aun sin fila en el catalogo de prueba

            creados = (await datos.execute(text(
                "SELECT count(*) FROM estudiantes WHERE codigo_estudiante LIKE '%-%' AND dni <> '80100000'"
            ))).scalar()
            assert creados == body["estudiantes_creados"]

            # Repetir no duplica
            r = await ga.post("/admision/ingesta", json={"admitidos": admitidos}, headers=h(SUPERADMIN))
            assert r.json()["estudiantes_creados"] == 0
    finally:
        await datos.execute(text("DELETE FROM admision_postulantes"))
        await datos.commit()
