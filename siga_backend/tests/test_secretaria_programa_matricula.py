import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tests.helpers.jwt_helper import auth_headers
from tests.helpers.module_loader import load_module_component


@pytest.mark.asyncio
@pytest.mark.parametrize("pagada,estado", [(True, "confirmada"), (False, "pendiente")])
async def test_alta_manual_persiste_el_estado_segun_pago(db_session, fixture_estudiante, fixture_periodo, pagada, estado):
    await db_session.execute(text("UPDATE estudiantes SET pago_matricula = :pago WHERE id=100"), {"pago": pagada})
    await db_session.commit()
    app = FastAPI()
    app.include_router(load_module_component("mod-gestion-academica", "routes").router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as cliente:
        r = await cliente.post("/matriculas/", headers=auth_headers(1), json={
            "estudiante_id": 100, "programa_id": 1, "periodo_id": 1,
            "detalles": [{"unidad_didactica_id": 101, "creditos": 4}],
        })
    assert r.status_code == 201, r.text
    assert r.json()["estado_matricula"] == estado
    assert await db_session.scalar(text("SELECT estado_matricula FROM matriculas WHERE id=:id"), {"id": r.json()["id"]}) == estado
    eventos = (await db_session.execute(text(
        "SELECT event_type FROM outbox_events WHERE event_type LIKE 'gestion_academica.matricula.%'"
    ))).scalars().all()
    assert eventos == [f"gestion_academica.matricula.{estado}"]


@pytest.mark.asyncio
async def test_asignacion_obligatoria_y_padron_acotado(db_session, fixture_programa_y_unidad):
    await db_session.execute(text("INSERT INTO core_roles (id, name, created_at) VALUES (90, 'secretaria_programa', now())"))
    await db_session.execute(text("""
        INSERT INTO core_users (id, email, full_name, hashed_password, is_active, is_superuser, created_at)
        VALUES (90, 'secretaria.matricula@siga.edu', 'Secretaría Prueba', 'x', true, false, now())
    """))
    await db_session.execute(text("INSERT INTO core_user_roles(user_id, role_id) VALUES (90, 90)"))
    await db_session.execute(text("""
        INSERT INTO estudiantes (id, dni, codigo_estudiante, nombres, apellidos, programa_id,
                                 pago_matricula, metadata_admision, created_at, updated_at)
        VALUES
          (200, '90000100', 'P-100', 'Alumno', 'Con pago', 2, true,
           '{"PROGRAMA DE   ESTUDIOS":"Desarrollo de Sistemas de Información"}', now(), now()),
          (201, '90000200', 'P-200', 'Alumno', 'De otra carrera', 1, true,
           '{"PROGRAMA DE ESTUDIOS":"Enfermería Técnica"}', now(), now())
    """))
    await db_session.execute(text("""
        SELECT setval('core_users_id_seq', GREATEST((SELECT max(id) FROM core_users), nextval('core_users_id_seq')))
    """))
    await db_session.execute(text("""
        SELECT setval('perfiles_personal_id_seq',
                      GREATEST((SELECT max(id) FROM perfiles_personal), nextval('perfiles_personal_id_seq')))
    """))
    await db_session.commit()

    usuarios = FastAPI()
    usuarios.include_router(load_module_component("mod-usuarios", "routes").router)
    academica = FastAPI()
    academica.include_router(load_module_component("mod-gestion-academica", "routes").router)
    admin = auth_headers(1)
    secretaria = auth_headers(90)
    async with AsyncClient(transport=ASGITransport(app=usuarios), base_url="http://test") as u, \
               AsyncClient(transport=ASGITransport(app=academica), base_url="http://test") as a:
        r = await a.get("/estudiantes/", headers=secretaria)
        assert r.status_code == 409 and "Asigna un programa" in r.json()["detail"]

        cuenta = {"email": "secretaria.nueva@siga.edu", "full_name": "Nueva", "password": "Solo-Prueba-2026",
                  "role_ids": [90]}
        r = await u.post("/usuarios", json=cuenta, headers=admin)
        assert r.status_code == 422
        r = await u.post("/usuarios", json={**cuenta, "programa_estudio_id": 1}, headers=admin)
        assert r.status_code == 201, r.text
        assert r.json()["programa_estudio_id"] == 1

        r = await u.put("/usuarios/90", json={"programa_estudio_id": 999}, headers=admin)
        assert r.status_code == 422
        r = await u.put("/usuarios/90", json={"programa_estudio_id": 1}, headers=admin)
        assert r.status_code == 200, r.text
        assert r.json()["programa_estudio_id"] == 1
        r = await u.get("/usuarios/90", headers=admin)
        assert r.json()["programa_estudio_id"] == 1
        r = await u.put("/usuarios/90", json={"programa_estudio_id": None}, headers=admin)
        assert r.status_code == 422

        r = await a.get("/estudiantes/", params={"q": "90000100"}, headers=secretaria)
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        assert [(e["id"], e["programa_id"], e["pago_matricula"]) for e in r.json()] == [(200, 1, True)]
        r = await a.get("/estudiantes/", params={"q": "90000200"}, headers=secretaria)
        assert r.json() == []
        r = await a.get("/estudiantes/", params={"programa_id": 2}, headers=secretaria)
        assert r.status_code == 403

        catalogo = {"catalogo_implementado": True}
        r = await a.get("/estudiantes/", params={**catalogo, "programa_id": 1}, headers=admin)
        assert r.status_code == 200, r.text
        assert {e["id"] for e in r.json()} & {200, 201} == {200}

        r = await a.get("/estudiantes/", params={**catalogo, "programa_id": 999}, headers=admin)
        assert r.status_code == 422

        estudiante = {
            "dni": "90000300", "codigo_estudiante": "P-300", "nombres": "Nueva",
            "apellidos": "Estudiante", "programa_id": 999,
        }
        r = await a.post("/estudiantes/", params=catalogo, json=estudiante, headers=admin)
        assert r.status_code == 422
        r = await a.post("/estudiantes/", params=catalogo, json={**estudiante, "programa_id": 1}, headers=admin)
        assert r.status_code == 201, r.text
