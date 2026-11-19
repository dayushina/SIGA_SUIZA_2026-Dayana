"""Arranque en Railway: seeder seguro en produccion, validaciones de start_railway.sh
y respeto de ENABLE_NATS en los modulos."""
import asyncio
import importlib.util
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.identity.seeds import user_seeder as us
from app.core.identity.seeds.base_seeder import BaseSeeder, SeederFatalError
from app.core.identity.seeds.seeder_runner import SeederRunner

BACKEND_DIR = Path(__file__).resolve().parent.parent
MODULES_DIR = BACKEND_DIR / "modules"


# ---------------------------------------------------------------------------
# 1. Seeder de usuarios
# ---------------------------------------------------------------------------
@pytest.fixture
def production(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.delenv("SIGA_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("SIGA_ADMIN_EMAIL", raising=False)
    return monkeypatch


@pytest.fixture
def development(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.delenv("SIGA_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("SIGA_ADMIN_EMAIL", raising=False)
    return monkeypatch


def test_desarrollo_crea_solo_el_superadmin_con_clave_generada(development):
    seeder = us.UserSeeder(db=None)
    data = seeder.get_users_data()
    assert len(data) == 1
    admin = data[0]
    assert admin["email"] == "admin@siga.edu" and admin["roles"] == ["superadmin"] and admin["is_superuser"]
    assert len(admin["password"]) >= us.MIN_ADMIN_PASSWORD_LENGTH
    assert admin["password"] not in us.PUBLIC_PASSWORDS
    assert seeder.generated_password == admin["password"]
    # cada reconstruccion genera una clave distinta (nunca una fija/publica)
    assert us.UserSeeder(db=None).get_users_data()[0]["password"] != admin["password"]


def test_desarrollo_usa_las_variables_del_superadmin(development):
    development.setenv("SIGA_ADMIN_PASSWORD", "Clave-Local-Segura-2026")
    development.setenv("SIGA_ADMIN_EMAIL", "direccion@suiza.edu.pe")
    seeder = us.UserSeeder(db=None)
    data = seeder.get_users_data()
    assert [(u["email"], u["password"]) for u in data] == [("direccion@suiza.edu.pe", "Clave-Local-Segura-2026")]
    assert seeder.generated_password is None


@pytest.mark.parametrize("pwd", ["corta", "admin123", "docente123"])
def test_desarrollo_rechaza_password_debil_o_publica(development, pwd):
    development.setenv("SIGA_ADMIN_PASSWORD", pwd)
    with pytest.raises(SeederFatalError):
        us.UserSeeder(db=None).get_users_data()


@pytest.mark.parametrize("env", ["development", "production"])
def test_ningun_entorno_crea_cuentas_de_prueba(monkeypatch, env):
    monkeypatch.setattr(settings, "environment", env)
    monkeypatch.setenv("SIGA_ADMIN_PASSWORD", "Una-Clave-Larga-2026!")
    monkeypatch.delenv("SIGA_ADMIN_EMAIL", raising=False)
    emails = {u["email"] for u in us.UserSeeder(db=None).get_users_data()}
    assert emails == {"admin@siga.edu"}


def test_produccion_sin_password_impide_arranque(production):
    with pytest.raises(SeederFatalError, match="SIGA_ADMIN_PASSWORD"):
        us.UserSeeder(db=None).get_users_data()


@pytest.mark.parametrize("pwd", ["corta", "admin123", "docente123"])
def test_produccion_rechaza_password_debil_o_publica(production, pwd):
    production.setenv("SIGA_ADMIN_PASSWORD", pwd)
    with pytest.raises(SeederFatalError):
        us.UserSeeder(db=None).get_users_data()


@pytest.mark.parametrize("env", ["production", "PROD", " Production "])
def test_produccion_crea_solo_el_superadmin(production, env):
    production.setattr(settings, "environment", env)
    production.setenv("SIGA_ADMIN_PASSWORD", "Una-Clave-Larga-2026!")
    production.setenv("SIGA_ADMIN_EMAIL", "direccion@suiza.edu.pe")
    data = us.UserSeeder(db=None).get_users_data()
    assert data == [{
        "email": "direccion@suiza.edu.pe",
        "password": "Una-Clave-Larga-2026!",
        "full_name": "Super Admin",
        "roles": ["superadmin"],
        "is_superuser": True,
    }]


async def test_runner_propaga_error_fatal_y_silencia_los_demas():
    class Falla(BaseSeeder):
        def get_dependencies(self):
            return []

        async def run(self):
            raise ValueError("error recuperable")

    class Fatal(BaseSeeder):
        def get_dependencies(self):
            return []

        async def run(self):
            raise SeederFatalError("config invalida")

    runner = SeederRunner(db=None)
    runner.seeders = {"Falla": Falla(None)}
    assert await runner.run_all() == ["Falla"]  # error comun: se registra y continua

    runner = SeederRunner(db=None)
    runner.seeders = {"Fatal": Fatal(None)}
    with pytest.raises(SeederFatalError):
        await runner.run_all()


async def test_produccion_con_usuarios_existentes_no_exige_password(production):
    """Si la BD ya tiene usuarios (p.ej. dump importado) no se pide SIGA_ADMIN_PASSWORD."""
    seeder = us.UserSeeder(db=None)

    async def ya_hay_usuarios():
        return False

    seeder.should_run = ya_hay_usuarios
    assert await seeder.run() == []


# ---------------------------------------------------------------------------
# 2. start_railway.sh: validaciones previas
# ---------------------------------------------------------------------------
SH = shutil.which("sh")


def _run_start_script(env_extra):
    env = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    env.update(env_extra)
    return subprocess.run(
        [SH, "./start_railway.sh"], cwd=BACKEND_DIR, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )


@pytest.mark.skipif(SH is None, reason="requiere un shell POSIX (sh)")
def test_script_falla_sin_secret_key():
    r = _run_start_script({"DATABASE_URL": "postgresql://u:p@h:5432/db"})
    assert r.returncode == 1
    assert "SECRET_KEY" in r.stderr


@pytest.mark.skipif(SH is None, reason="requiere un shell POSIX (sh)")
def test_script_falla_con_secret_key_corta():
    r = _run_start_script({"SECRET_KEY": "x" * 31, "DATABASE_URL": "postgresql://u:p@h:5432/db"})
    assert r.returncode == 1
    assert "SECRET_KEY" in r.stderr


@pytest.mark.skipif(SH is None, reason="requiere un shell POSIX (sh)")
def test_script_falla_sin_base_de_datos():
    r = _run_start_script({"SECRET_KEY": "x" * 48})
    assert r.returncode == 1
    assert "DATABASE_URL" in r.stderr


def _fake_bin(tmp_path, name, exit_code, marker):
    """Ejecutable falso en el PATH: registra que se llamo y sale con exit_code."""
    fake = tmp_path / name
    fake.write_text(f"#!/bin/sh\necho '{marker}' >> '{(tmp_path / 'calls.log').as_posix()}'\n"
                    f"echo '{marker} fallo' >&2\nexit {exit_code}\n", encoding="utf-8")
    fake.chmod(0o755)


def _calls(tmp_path):
    log = tmp_path / "calls.log"
    return log.read_text().split() if log.exists() else []


def _run_with_fakes(tmp_path):
    return _run_start_script({
        "SECRET_KEY": "x" * 48,
        "DATABASE_URL": "postgresql://u:p@127.0.0.1:1/db",
        "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
    })


@pytest.mark.skipif(SH is None, reason="requiere un shell POSIX (sh)")
def test_script_falla_si_las_migraciones_fallan(tmp_path):
    # La preparacion de la BD (python -m bootstrap.restore_seed) va bien; alembic falla:
    # el script debe abortar (antes seguia de largo).
    _fake_bin(tmp_path, "python", 0, "python")
    _fake_bin(tmp_path, "alembic", 1, "alembic")
    r = _run_with_fakes(tmp_path)
    assert r.returncode == 1
    assert "alembic upgrade head fallo" in r.stderr
    assert "Iniciando Core Gateway" not in r.stdout
    assert _calls(tmp_path) == ["python", "alembic"]  # el snapshot va ANTES de las migraciones


@pytest.mark.skipif(SH is None, reason="requiere un shell POSIX (sh)")
def test_script_falla_si_no_puede_preparar_la_bd(tmp_path):
    # Si la restauracion del snapshot falla, no se migra ni se arranca nada.
    _fake_bin(tmp_path, "python", 1, "python")
    _fake_bin(tmp_path, "alembic", 0, "alembic")
    r = _run_with_fakes(tmp_path)
    assert r.returncode == 1
    assert "no se pudo preparar la base de datos" in r.stderr
    assert "Iniciando Core Gateway" not in r.stdout
    assert _calls(tmp_path) == ["python"]


# ---------------------------------------------------------------------------
# 3. Modulos: ENABLE_NATS=false evita conectar al broker
# ---------------------------------------------------------------------------
class _FakeNATS:
    instancias = 0

    def __init__(self):
        type(self).instancias += 1
        self.is_connected = False

    async def connect(self, url):
        raise ConnectionError("no hay broker")


def _load_module_file(monkeypatch, path: Path, enable_nats: str):
    """Carga outbox_worker/event_listener con sus dependencias locales simuladas."""
    monkeypatch.setenv("ENABLE_NATS", enable_nats)
    fake_db = types.ModuleType("database")

    def _session_que_falla():
        raise RuntimeError("sin BD en el test")

    fake_db.AsyncSessionLocal = _session_que_falla
    fake_db.OutboxEvent = object
    fake_models = types.ModuleType("models")
    fake_models.AuditLog = object
    monkeypatch.setitem(sys.modules, "database", fake_db)
    monkeypatch.setitem(sys.modules, "models", fake_models)

    spec = importlib.util.spec_from_file_location(f"_test_{path.parent.name}_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.NATS = _FakeNATS
    _FakeNATS.instancias = 0
    return mod


async def _run_briefly(coro, seconds=0.2):
    task = asyncio.create_task(coro)
    await asyncio.sleep(seconds)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


OUTBOX_WORKERS = sorted(MODULES_DIR.glob("*/outbox_worker.py"))
LISTENERS = sorted(MODULES_DIR.glob("*/event_listener.py"))


@pytest.mark.parametrize("path", OUTBOX_WORKERS, ids=lambda p: p.parent.name)
@pytest.mark.parametrize("enable,esperadas", [("false", 0), ("0", 0), ("true", 1)])
async def test_outbox_worker_respeta_enable_nats(monkeypatch, path, enable, esperadas):
    mod = _load_module_file(monkeypatch, path, enable)
    await _run_briefly(mod.outbox_worker())
    assert _FakeNATS.instancias == esperadas


@pytest.mark.parametrize("path", LISTENERS, ids=lambda p: p.parent.name)
@pytest.mark.parametrize("enable,esperadas", [("false", 0), ("true", 1)])
async def test_event_listener_respeta_enable_nats(monkeypatch, path, enable, esperadas):
    mod = _load_module_file(monkeypatch, path, enable)
    await asyncio.wait_for(mod.start_listener(), timeout=5)
    assert _FakeNATS.instancias == esperadas


def test_hay_modulos_cubiertos():
    assert len(OUTBOX_WORKERS) == 5 and len(LISTENERS) == 2
