"""Restaura el snapshot versionado en una BD vacia y endurece cuentas publicas.

Lo ejecuta start_railway.sh ANTES de las migraciones:

    python -m bootstrap.restore_seed

Politica (variable SIGA_SEED_MODE):
    auto     (defecto) Restaura solo si la BD esta vacia, o si solo contiene lo que
             un arranque previo sin datos genero automaticamente (roles, permisos,
             un admin...). Una BD con datos de negocio NUNCA se toca: tras la
             primera carga, produccion es la fuente de verdad.
    replace  Reemplaza la BD cuando el snapshot del repositorio es distinto del
             ultimo aplicado. Util ANTES de salir a produccion; quitala despues.
    skip     No restaura nada.

Toda la restauracion ocurre en UNA transaccion: si algo falla (incluida la
verificacion de filas por tabla) no queda nada a medias.

Codigos de salida: 0 ok / 1 error de configuracion o de restauracion.
"""
from __future__ import annotations

import io
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import bcrypt
import psycopg2

from bootstrap.seed_format import CopyBlock, SeedError, iter_data_section, unpack

SEED_FILE = Path(__file__).resolve().parent / "seed" / "siga_seed.enc"
LOCK_ID = 7_301_2026  # pg_advisory_xact_lock: evita restauraciones concurrentes
META_SCHEMA = "siga_meta"

# Tablas que el propio sistema llena al arrancar sobre una BD vacia (Core seeder,
# registro de modulos y la auditoria/outbox de esa actividad). Verificado
# arrancando la imagen de Railway sobre Postgres vacio con SIGA_SEED_MODE=skip.
# Si SOLO estas tienen filas, la BD no tiene datos de negocio y es seguro
# reemplazarla por el snapshot.
AUTO_GENERATED_TABLES = {
    "alembic_version", "core_roles", "core_permissions", "core_role_permissions",
    "core_users", "core_user_roles", "core_modules", "core_audit_logs",
    "outbox_events",
}
MAX_AUTO_USERS = 1  # el arranque sin datos crea un unico superadmin

PRODUCTION_ENVIRONMENTS = {"production", "prod"}
MIN_ADMIN_PASSWORD_LENGTH = 12


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def log(msg: str) -> None:
    print(f"[seed] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Conexion
# ---------------------------------------------------------------------------
def dsn_from_env(env=os.environ) -> str:
    url = env.get("DATABASE_URL") or env.get("DB_URL")
    if url:
        scheme, rest = url.split("://", 1)
        return "postgresql://" + rest if scheme.startswith("postgres") else url
    if not env.get("DB_PASSWORD"):
        raise SeedError("Sin conexion a BD: define DATABASE_URL (o DB_HOST/DB_USER/DB_PASSWORD/DB_NAME).")
    host = env.get("DB_HOST", "localhost")
    if host == "host.docker.internal" and env.get("DB_HOST_LOCAL"):
        host = env["DB_HOST_LOCAL"]
    return psycopg2.extensions.make_dsn(
        host=host, port=env.get("DB_PORT", "5432"), user=env.get("DB_USER", "postgres"),
        password=env["DB_PASSWORD"], dbname=env.get("DB_NAME", "siga_local"),
    )


def connect_with_retry(dsn: str, wait_seconds: int):
    """En el primer despliegue Postgres puede tardar en aceptar conexiones."""
    deadline = time.monotonic() + wait_seconds
    attempt = 0
    while True:
        attempt += 1
        try:
            return psycopg2.connect(dsn, connect_timeout=10)
        except psycopg2.OperationalError as exc:
            if time.monotonic() >= deadline:
                raise SeedError(f"La BD no respondio en {wait_seconds}s: {str(exc).strip()}") from exc
            log(f"BD no disponible aun (intento {attempt}); reintentando en 3s...")
            time.sleep(3)


# ---------------------------------------------------------------------------
# Estado de la BD y decision
# ---------------------------------------------------------------------------
@dataclass
class DbState:
    public_tables: int
    applied_seed_id: Optional[str]
    business_rows: Dict[str, int]   # tablas con filas fuera de AUTO_GENERATED_TABLES
    users: int


def read_state(cur) -> DbState:
    cur.execute("SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public'")
    public_tables = cur.fetchone()[0]

    applied = None
    cur.execute("SELECT to_regclass(%s)", (f"{META_SCHEMA}.seed_history",))
    if cur.fetchone()[0]:
        cur.execute(f"SELECT seed_id FROM {META_SCHEMA}.seed_history ORDER BY applied_at DESC, id DESC LIMIT 1")
        row = cur.fetchone()
        applied = row[0] if row else None

    business: Dict[str, int] = {}
    users = 0
    if public_tables:
        cur.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public' ORDER BY 1")
        for (table,) in cur.fetchall():
            if table == "core_users":
                cur.execute('SELECT count(*) FROM public."core_users"')
                users = cur.fetchone()[0]
                continue
            if table in AUTO_GENERATED_TABLES:
                continue
            cur.execute(f"SELECT EXISTS (SELECT 1 FROM public.{qident(table)})")
            if cur.fetchone()[0]:
                cur.execute(f"SELECT count(*) FROM public.{qident(table)}")
                business[table] = cur.fetchone()[0]
    return DbState(public_tables, applied, business, users)


def decide(mode: str, state: DbState, seed_id: Optional[str]) -> Tuple[str, str]:
    """Devuelve (accion, motivo). accion: 'restore' | 'replace' | 'none'."""
    if mode == "skip":
        return "none", "SIGA_SEED_MODE=skip"
    if seed_id is None:
        return "none", "no hay snapshot en el repositorio"
    if state.public_tables == 0:
        return "restore", "BD vacia"
    if state.applied_seed_id == seed_id:
        return "none", f"el snapshot {seed_id[:12]} ya esta aplicado"
    if mode == "replace":
        return "replace", "SIGA_SEED_MODE=replace y el snapshot del repositorio es distinto del aplicado"
    if state.applied_seed_id:
        return "none", (f"la BD ya fue cargada con el snapshot {state.applied_seed_id[:12]}; produccion es la "
                        f"fuente de verdad. Hay uno nuevo ({seed_id[:12]}) que NO se aplica "
                        "(usa SIGA_SEED_MODE=replace si de verdad quieres sobrescribir)")
    if not state.business_rows and state.users <= MAX_AUTO_USERS:
        return "replace", "la BD solo contiene datos generados automaticamente por un arranque previo sin datos"
    tablas = ", ".join(f"{t}={n}" for t, n in sorted(state.business_rows.items())[:5]) or f"core_users={state.users}"
    return "none", (f"la BD ya tiene datos que no provienen del snapshot ({tablas}); no se toca. "
                    "Usa SIGA_SEED_MODE=replace para sobrescribirla")


# ---------------------------------------------------------------------------
# Restauracion
# ---------------------------------------------------------------------------
def restore(conn, payload: dict, replace: bool) -> Dict[str, int]:
    """Aplica el snapshot dentro de la transaccion abierta en `conn` (sin commit)."""
    sections = payload["sections"]
    with conn.cursor() as cur:
        if replace:
            log("Eliminando el esquema public actual...")
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
        log("Creando tablas (pre-data)...")
        cur.execute(sections["pre_data"])
        log("Cargando datos...")
        loaded: Dict[str, int] = {}
        for st in iter_data_section(sections["data"]):
            if isinstance(st, CopyBlock):
                if st.count:
                    cur.copy_expert(st.statement, io.StringIO(st.rows))
                loaded[st.table] = loaded.get(st.table, 0) + st.count
            else:
                cur.execute(st)
        log("Creando claves, indices y relaciones (post-data)...")
        cur.execute(sections["post_data"])

        log("Verificando filas por tabla...")
        mismatches: List[str] = []
        for table, expected in sorted(payload["counts"].items()):
            cur.execute(f"SELECT count(*) FROM public.{qident(table)}")
            got = cur.fetchone()[0]
            if got != expected:
                mismatches.append(f"{table}: esperado {expected}, restaurado {got}")
        if mismatches:
            raise SeedError("Restauracion inconsistente: " + "; ".join(mismatches))

        cur.execute(f"""
            CREATE SCHEMA IF NOT EXISTS {META_SCHEMA};
            CREATE TABLE IF NOT EXISTS {META_SCHEMA}.seed_history (
                id          serial PRIMARY KEY,
                seed_id     text NOT NULL,
                created_at  text,
                applied_at  timestamptz NOT NULL DEFAULT now(),
                mode        text NOT NULL,
                tables      integer NOT NULL,
                total_rows  bigint NOT NULL
            )""")
        cur.execute(
            f"INSERT INTO {META_SCHEMA}.seed_history (seed_id, created_at, mode, tables, total_rows) "
            "VALUES (%s, %s, %s, %s, %s)",
            (payload["seed_id"], payload.get("created_at"), "replace" if replace else "restore",
             len(payload["counts"]), sum(payload["counts"].values())),
        )
    return payload["counts"]


# ---------------------------------------------------------------------------
# Endurecimiento: cuentas de desarrollo con contrasenas publicas
# ---------------------------------------------------------------------------
def harden_public_accounts(conn, dev_users: List[dict], env=os.environ) -> List[str]:
    """En produccion, ninguna cuenta puede conservar una contrasena publicada en el repo.

    - El superadmin (SIGA_ADMIN_EMAIL, por defecto admin@siga.edu) recibe SIGA_ADMIN_PASSWORD.
    - Las demas cuentas de prueba con su contrasena publica se desactivan.
    Devuelve las acciones realizadas. No hace commit.
    """
    admin_email = (env.get("SIGA_ADMIN_EMAIL") or "admin@siga.edu").strip().lower()
    admin_password = env.get("SIGA_ADMIN_PASSWORD", "")
    public = {u["email"].lower(): u["password"] for u in dev_users}
    actions: List[str] = []
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.core_users')")
        if not cur.fetchone()[0]:
            return actions
        cur.execute("SELECT id, lower(email), hashed_password FROM public.core_users WHERE is_active")
        for user_id, email, hashed in cur.fetchall():
            pwd = public.get(email)
            if not pwd or not hashed:
                continue
            try:
                compromised = bcrypt.checkpw(pwd.encode(), hashed.encode())
            except ValueError:
                compromised = False
            if not compromised:
                continue
            if email == admin_email:
                if len(admin_password) < MIN_ADMIN_PASSWORD_LENGTH or admin_password in public.values():
                    raise SeedError(
                        f"El superadmin {email} tiene la contrasena publica del repositorio. Define "
                        f"SIGA_ADMIN_PASSWORD (minimo {MIN_ADMIN_PASSWORD_LENGTH} caracteres) para reemplazarla.")
                new_hash = bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt()).decode()
                cur.execute("UPDATE public.core_users SET hashed_password = %s WHERE id = %s", (new_hash, user_id))
                actions.append(f"{email}: contrasena publica reemplazada por SIGA_ADMIN_PASSWORD")
            else:
                cur.execute("UPDATE public.core_users SET is_active = false WHERE id = %s", (user_id,))
                actions.append(f"{email}: desactivada (tenia la contrasena publica del repositorio)")
    return actions


def _dev_users() -> List[dict]:
    from app.core.identity.seeds.user_seeder import DEV_USERS
    return DEV_USERS


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------
def run(env=os.environ, seed_file: Path = SEED_FILE) -> int:
    mode = (env.get("SIGA_SEED_MODE") or "auto").strip().lower()
    if mode not in {"auto", "replace", "skip"}:
        raise SeedError(f"SIGA_SEED_MODE invalido: {mode!r} (usa auto, replace o skip)")
    production = (env.get("ENVIRONMENT") or "").strip().lower() in PRODUCTION_ENVIRONMENTS

    seed_blob = seed_file.read_bytes() if seed_file.exists() else None
    payload = None
    seed_id = None
    if seed_blob is not None:
        manifest = seed_file.with_suffix(".manifest")
        seed_id = manifest.read_text(encoding="utf-8").split()[0] if manifest.exists() else None

    conn = connect_with_retry(dsn_from_env(env), int(env.get("SIGA_DB_WAIT_SECONDS", "90")))
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
            cur.execute("SHOW server_version")
            server_version = cur.fetchone()[0]
            state = read_state(cur)

        if seed_blob is not None and seed_id is None and mode != "skip":
            # Sin manifiesto hay que descifrar para conocer el seed_id.
            payload = _unpack(seed_blob, env)
            seed_id = payload["seed_id"]

        action, reason = decide(mode, state, seed_id)
        log(f"PostgreSQL {server_version} | modo={mode} | tablas en public={state.public_tables} | "
            f"snapshot aplicado={(state.applied_seed_id or '-')[:12]} | snapshot repo={(seed_id or '-')[:12]}")
        if action == "none":
            log(f"Sin restauracion: {reason}.")
        else:
            log(f"Restaurando snapshot ({reason})...")
            if payload is None:
                payload = _unpack(seed_blob, env)
            if payload["seed_id"] != seed_id:
                raise SeedError("El manifiesto no coincide con el snapshot cifrado (vuelve a exportar).")
            t0 = time.monotonic()
            counts = restore(conn, payload, replace=(action == "replace"))
            log(f"Snapshot {seed_id[:12]} (exportado {payload.get('created_at')}, origen PostgreSQL "
                f"{payload.get('source')}) restaurado: {len(counts)} tablas, {sum(counts.values())} filas "
                f"en {time.monotonic() - t0:.1f}s.")

        if production:
            for action_msg in harden_public_accounts(conn, _dev_users(), env):
                log(f"Seguridad: {action_msg}")
        conn.commit()
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _unpack(blob: bytes, env) -> dict:
    key = env.get("SIGA_SEED_KEY", "")
    if not key:
        raise SeedError("Hay que restaurar el snapshot pero falta SIGA_SEED_KEY. Copiala de tu .env local "
                        "a las Variables del servicio en Railway.")
    return unpack(blob, key)


def main() -> int:
    try:
        return run()
    except SeedError as exc:
        print(f"[seed] ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    except psycopg2.Error as exc:
        print(f"[seed] ERROR de PostgreSQL (nada se aplico, transaccion revertida): {str(exc).strip()}",
              file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
