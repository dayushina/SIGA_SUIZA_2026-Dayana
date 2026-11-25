"""Flujo completo del boton "Sincronizar desde Admision" con la plantilla real.

Replica los dos pasos separados en SIGA:
  1. Administración publica el archivo en POST mod-admision /publicaciones/excel
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
from tests.test_ingesta_admision import SECRETARIA, SUPERADMIN, datos, h  # noqa: F401 (fixture)

PLANTILLA = Path(__file__).resolve().parents[2] / "ADMISION_2026.xlsx"
ADMIN_ADMISION = 92


@pytest.mark.asyncio
async def test_sincronizacion_completa_con_plantilla_real(datos):  # noqa: F811
    if not PLANTILLA.exists():
        pytest.skip("ADMISION_2026.xlsx no esta en la raiz del proyecto")

    adm_main = load_module_component("mod-admision", "main")
    adm_db = __import__("database")
    async with adm_db.engine.begin() as conn:
        await conn.run_sync(adm_db.Base.metadata.create_all, checkfirst=True)
    await datos.execute(text("DELETE FROM admision_postulantes"))
    await datos.execute(text("""
        INSERT INTO core_roles (id, name, created_at) VALUES (:id, 'admin_admision', now())
    """), {"id": ADMIN_ADMISION})
    await datos.execute(text("""
        INSERT INTO core_users (id, email, full_name, hashed_password, is_active, is_superuser, created_at)
        VALUES (:id, 'publicador@siga.test', 'Publicador', 'x', true, false, now())
    """), {"id": ADMIN_ADMISION})
    await datos.execute(text("INSERT INTO core_user_roles (user_id, role_id) VALUES (:id, :id)"),
                        {"id": ADMIN_ADMISION})
    await datos.execute(text("""
        INSERT INTO admision_postulantes (dni, sincronizado)
        VALUES ('99000001', true), ('99000002', false)
    """))
    await datos.commit()
    async with adm_main.app.router.lifespan_context(adm_main.app):
        assert (await datos.execute(text("SELECT dni FROM admision_postulantes"))).scalars().all() == ["99000002"]
    await datos.commit()

    routes = load_module_component("mod-gestion-academica", "routes")
    gestion = FastAPI()
    gestion.include_router(routes.router)

    try:
        async with AsyncClient(transport=ASGITransport(app=adm_main.app), base_url="http://adm") as adm, \
                   AsyncClient(transport=ASGITransport(app=gestion), base_url="http://ga") as ga:
            publicacion = "/publicaciones/excel"
            ejemplo = {"file": ("restringido.xlsx", b"no debe procesarse")}
            assert (await adm.post("/upload", files=ejemplo, headers=h(SUPERADMIN))).status_code == 404
            assert (await adm.post(publicacion, files=ejemplo)).status_code == 401
            assert (await adm.post(publicacion, files=ejemplo, headers=h(SECRETARIA))).status_code == 403
            assert (await adm.get("/resumen")).json()["pendientes"] == 1
            assert await datos.scalar(text("SELECT count(*) FROM estudiantes")) == 1
            # 1. Carga del Excel
            with PLANTILLA.open("rb") as f:
                r = await adm.post(publicacion, headers=h(ADMIN_ADMISION), files={"file": ("ADMISION_2026.xlsx", f,
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
            assert r.status_code == 200, r.text
            total = r.json()["total"]
            assert total > 700
            assert r.json()["sin_carrera_reconocida"] == 0
            assert r.json()["pendientes"] == total
            assert await datos.scalar(text("SELECT count(*) FROM estudiantes")) == 1

            # 2. Lista normalizada
            r = await adm.get("/admitidos")
            assert r.status_code == 200
            admitidos = r.json()["admitidos"]
            assert len(admitidos) == total
            assert all(a["programa_id"] for a in admitidos)
            assert r.headers["cache-control"] == "no-store"

            # Un fallo por fila debe conservar esa fila y consumir todas las demás.
            ultimo = admitidos[-1]
            lote = [*admitidos[:-1], {**ultimo, "programa_id": None}]
            r = await ga.post("/admision/ingesta", json={"admitidos": lote}, headers=h(SUPERADMIN))
            assert r.status_code == 202, r.text
            body = r.json()
            assert [fila["dni"] for fila in body["sin_programa"]] == [ultimo["dni"]]
            assert body["con_error"] == []
            assert body["estudiantes_creados"] + body["ya_existentes"] == total - 1
            assert 1 in body["programas_sin_plan"]  # Sistemas aun sin fila en el catalogo de prueba

            creados = (await datos.execute(text(
                "SELECT count(*) FROM estudiantes WHERE codigo_estudiante LIKE '%-%' AND dni <> '80100000'"
            ))).scalar()
            assert creados == body["estudiantes_creados"]

            r = await adm.get("/admitidos?solo_pendientes=true")
            assert r.json()["admitidos"] == [ultimo]
            r = await ga.post("/admision/ingesta", json={"admitidos": [ultimo]}, headers=h(SUPERADMIN))
            assert r.json()["estudiantes_creados"] == 1
            assert (await adm.get("/resumen")).json() == {"total": 0, "pendientes": 0, "sincronizados": 0}
            assert (await adm.get("/admitidos?solo_pendientes=true")).json()["admitidos"] == []
            dnis = [a["dni"] for a in admitidos if a["dni"] != "80100000"][:2]
            filas = (await datos.execute(text(
                "SELECT dni, id FROM estudiantes WHERE dni IN (:primero, :segundo)"
            ), {"primero": dnis[0], "segundo": dnis[1]})).all()
            ids = dict(filas)
            # Las bajas no reponen las filas de Admisión. Repetir el envío viejo tampoco.
            r = await ga.delete(f"/estudiantes/{ids[dnis[0]]}", params={"motivo": "Baja de prueba"},
                                headers=h(SUPERADMIN))
            assert r.status_code == 200, r.text
            r = await ga.post("/estudiantes/lote/eliminar",
                              json={"ids": [ids[dnis[1]]], "motivo": "Baja de prueba", "confirmacion": "ELIMINAR"},
                              headers=h(SUPERADMIN))
            assert r.status_code == 200 and r.json()["eliminados"] == [ids[dnis[1]]], r.text
            assert (await adm.get("/resumen")).json()["pendientes"] == 0
            assert (await adm.get("/admitidos")).json()["admitidos"] == []
            r = await ga.post("/admision/ingesta", json={"admitidos": admitidos}, headers=h(SUPERADMIN))
            assert r.json()["estudiantes_creados"] == 0
            assert r.json()["fuera_de_bandeja"] == total

            # Una carga nueva del mismo archivo puede volver a inscribir a los dos.
            with PLANTILLA.open("rb") as f:
                r = await adm.post(publicacion, headers=h(SUPERADMIN), files={"file": ("ADMISION_2026.xlsx", f,
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
            assert r.status_code == 200
            assert r.json()["pendientes"] == 2
            assert r.json()["ya_registrados"] == total - 2
            r = await adm.get("/resumen")
            assert r.json() == {"total": 2, "pendientes": 2, "sincronizados": 0}
            r = await adm.get("/admitidos?solo_pendientes=true")
            pendientes = r.json()["admitidos"]
            assert [a["dni"] for a in pendientes] == dnis
            r = await ga.post("/admision/ingesta", json={"admitidos": admitidos[:2]}, headers=h(SUPERADMIN))
            assert r.json()["fuera_de_bandeja"] == 2
            assert (await adm.get("/resumen")).json()["pendientes"] == 2
            r = await adm.post(publicacion, headers=h(SUPERADMIN), files={"file": ("invalido.xlsx", b"no es un Excel")})
            assert r.status_code == 400
            assert (await adm.get("/resumen")).json()["pendientes"] == 2

            r = await ga.post("/admision/ingesta", json={"admitidos": pendientes}, headers=h(SUPERADMIN))
            assert r.json()["estudiantes_creados"] == 2
            assert (await adm.get("/resumen")).json()["pendientes"] == 0
            assert await datos.scalar(text("SELECT count(*) FROM admision_postulantes")) == 0
    finally:
        await datos.execute(text("DELETE FROM admision_postulantes"))
        await datos.commit()
        await adm_db.engine.dispose()
