"""Contrato de seguridad de los endpoints /auth del Core.

- Las credenciales viajan en el body JSON (nunca en la query string, que queda en logs).
- /auth/register no es publico: requiere token de superadmin.
- La configuracion rechaza claves JWT inseguras.
Todas las lecturas a BD apuntan a siga_test (ver conftest.py).
"""
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.identity.auth_service import AuthService


@pytest.fixture
async def client_core():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test-core") as client:
        yield client


async def test_login_rechaza_credenciales_en_query_string(client_core):
    r = await client_core.post("/auth/login", params={"email": "x@siga.edu", "password": "secreto"})
    assert r.status_code == 422


async def test_login_con_body_json_credenciales_invalidas_401(client_core):
    r = await client_core.post("/auth/login", json={"email": "no-existe@siga.test", "password": "incorrecta"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Credenciales inválidas"


async def test_register_sin_token_401(client_core):
    r = await client_core.post("/auth/register", json={"email": "nuevo@siga.test", "password": "Clave12345"})
    assert r.status_code == 401


async def test_register_usuario_no_superadmin_403(client_core, monkeypatch):
    async def fake_current_user(self, token):
        return {"id": 99, "role": "docente", "roles": ["docente"]}
    monkeypatch.setattr(AuthService, "get_current_user", fake_current_user)

    r = await client_core.post(
        "/auth/register",
        json={"email": "nuevo@siga.test", "password": "Clave12345"},
        headers={"Authorization": "Bearer token-de-prueba"},
    )
    assert r.status_code == 403


async def test_register_superadmin_permitido(client_core, monkeypatch):
    async def fake_current_user(self, token):
        return {"id": 1, "role": "superadmin", "roles": ["superadmin"]}

    async def fake_register(self, email, password, full_name=None):
        return {"success": True, "user": {"id": 500, "email": email, "full_name": full_name}}

    monkeypatch.setattr(AuthService, "get_current_user", fake_current_user)
    monkeypatch.setattr(AuthService, "register_user", fake_register)

    r = await client_core.post(
        "/auth/register",
        json={"email": "nuevo@siga.test", "password": "Clave12345", "full_name": "Nuevo"},
        headers={"Authorization": "Bearer token-de-prueba"},
    )
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "nuevo@siga.test"


@pytest.mark.parametrize("clave", ["", "change-me-in-production", "corta"])
def test_config_rechaza_secret_key_insegura(clave):
    from app.core.config import CoreSettings
    with pytest.raises(ValueError):
        CoreSettings(secret_key=clave, _env_file=None)


def test_config_acepta_secret_key_robusta():
    from app.core.config import CoreSettings
    s = CoreSettings(secret_key="x" * 48, _env_file=None)
    assert len(s.secret_key) == 48
