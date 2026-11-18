"""Conexion a PostgreSQL para los scripts de mantenimiento de la raiz.

Las credenciales NUNCA se escriben en el codigo: se leen de variables de entorno
o, en su defecto, del archivo ".env" de la raiz (no versionado, ver .env.example).

Como estos scripts corren en el host (no en Docker), DB_HOST=host.docker.internal
se traduce a "localhost". Se puede forzar otro host con DB_HOST_LOCAL.
"""
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent


def load_env(path: Path = _ROOT / ".env") -> None:
    """Carga KEY=VALUE de .env sin sobrescribir variables ya definidas."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def db_params() -> dict:
    load_env()
    password = os.environ.get("DB_PASSWORD")
    if not password:
        raise SystemExit("DB_PASSWORD no definido: configuralo en .env o en el entorno.")
    host = os.environ.get("DB_HOST_LOCAL") or os.environ.get("DB_HOST", "localhost")
    if host == "host.docker.internal":
        host = "localhost"
    return {
        "host": host,
        "port": int(os.environ.get("DB_PORT", "5432")),
        "user": os.environ.get("DB_USER", "postgres"),
        "password": password,
        "dbname": os.environ.get("DB_NAME", "siga_local"),
    }


def connect_db():
    import psycopg2
    return psycopg2.connect(**db_params())
