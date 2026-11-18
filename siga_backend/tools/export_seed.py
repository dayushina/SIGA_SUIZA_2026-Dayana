"""Exporta la BD local a un snapshot cifrado que Railway restaura automaticamente.

Uso (desde la raiz del proyecto):
    siga_backend\\venv\\Scripts\\python siga_backend\\tools\\export_seed.py

- Lee la conexion de .env (DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME).
- Usa una sola instantanea consistente (pg_dump -Fc) y la separa en
  pre-data / data / post-data con pg_restore.
- Cifra con SIGA_SEED_KEY; si no existe la genera y la agrega a .env.
- Escribe siga_backend/bootstrap/seed/siga_seed.enc y siga_seed.manifest.
  Si el contenido no cambio, no reescribe nada (evita commits innecesarios).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from bootstrap.seed_format import (  # noqa: E402
    FORMAT_VERSION, SECTIONS, compute_seed_id, count_rows, generate_key, normalize_sql, pack, unpack,
)

ENV_FILE = ROOT / ".env"
SEED_DIR = BACKEND / "bootstrap" / "seed"
SEED_FILE = SEED_DIR / "siga_seed.enc"
MANIFEST = SEED_DIR / "siga_seed.manifest"


def load_env() -> None:
    if not ENV_FILE.exists():
        sys.exit(f"Falta {ENV_FILE} (copia .env.example).")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def ensure_key() -> str:
    key = os.environ.get("SIGA_SEED_KEY", "").strip()
    if key:
        return key
    key = generate_key()
    with ENV_FILE.open("a", encoding="utf-8") as fh:
        fh.write("\n# Clave del snapshot cifrado de la BD (bootstrap/seed). Copiala a las Variables\n"
                 "# de Railway. Sin ella no se puede restaurar. NUNCA la versiones.\n"
                 f"SIGA_SEED_KEY={key}\n")
    os.environ["SIGA_SEED_KEY"] = key
    print(f"  Nueva SIGA_SEED_KEY generada y guardada en {ENV_FILE.name}")
    return key


def find_pg_tool(name: str) -> str:
    override = os.environ.get("PG_BIN")
    candidates = [Path(override) / f"{name}.exe", Path(override) / name] if override else []
    if os.name == "nt":
        base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PostgreSQL"
        versions = sorted((p for p in base.glob("*") if p.name.isdigit()), key=lambda p: int(p.name), reverse=True)
        candidates += [v / "bin" / f"{name}.exe" for v in versions]
    for c in candidates:
        if c.exists():
            return str(c)
    found = shutil.which(name)
    if not found:
        sys.exit(f"No se encontro {name}. Define PG_BIN con la carpeta bin de PostgreSQL.")
    return found


def main() -> int:
    load_env()
    host = os.environ.get("DB_HOST", "localhost")
    if host == "host.docker.internal":
        host = os.environ.get("DB_HOST_LOCAL", "localhost")
    db = os.environ.get("DB_NAME", "siga_local")
    conn_args = ["-h", host, "-p", os.environ.get("DB_PORT", "5432"), "-U", os.environ.get("DB_USER", "postgres")]
    env = {**os.environ, "PGPASSWORD": os.environ.get("DB_PASSWORD", "")}
    pg_dump, pg_restore = find_pg_tool("pg_dump"), find_pg_tool("pg_restore")

    print(f"Exportando {db}@{host} con {pg_dump}")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "siga.dump"
        subprocess.run([pg_dump, *conn_args, "-d", db, "-Fc", "--no-owner", "--no-privileges",
                        "--schema=public", "-f", str(archive)], env=env, check=True)
        sections = {}
        for name in SECTIONS:
            out = subprocess.run([pg_restore, "--section=" + name.replace("_", "-"), "--no-owner",
                                  "--no-privileges", "-f", "-", str(archive)],
                                 env=env, check=True, capture_output=True)
            sections[name] = normalize_sql(out.stdout.decode("utf-8"))
        version = subprocess.run([pg_dump, "--version"], capture_output=True, text=True).stdout.strip()

    counts = count_rows(sections["data"])
    seed_id = compute_seed_id(sections)
    key = ensure_key()

    if SEED_FILE.exists() and MANIFEST.exists() and MANIFEST.read_text(encoding="utf-8").split()[0] == seed_id:
        try:
            unpack(SEED_FILE.read_bytes(), key)
            print(f"Sin cambios: el snapshot {seed_id[:12]} ya refleja la BD. No se reescribio nada.")
            return 0
        except Exception:
            pass  # la clave cambio: reescribir con la clave actual

    payload = {
        "format": FORMAT_VERSION,
        "seed_id": seed_id,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": version,
        "sections": sections,
        "counts": counts,
    }
    blob = pack(payload, key)
    assert unpack(blob, key)["seed_id"] == seed_id  # verificacion de ida y vuelta

    SEED_DIR.mkdir(parents=True, exist_ok=True)
    SEED_FILE.write_bytes(blob)
    MANIFEST.write_text(f"{seed_id} {payload['created_at']} tablas={len(counts)} "
                        f"filas={sum(counts.values())}\n", encoding="utf-8")
    print(f"Snapshot {seed_id[:12]}: {len(counts)} tablas, {sum(counts.values())} filas, "
          f"{len(blob) / 1024:.0f} KB cifrado -> {SEED_FILE.relative_to(ROOT)}")
    print("Siguiente paso: git add siga_backend/bootstrap/seed && git commit && git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
