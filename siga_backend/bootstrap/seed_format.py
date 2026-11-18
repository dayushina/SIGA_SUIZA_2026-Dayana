"""Formato del snapshot de base de datos versionado en el repositorio.

El repositorio es PUBLICO y la BD contiene datos personales, por eso el snapshot
se guarda comprimido (gzip) y cifrado (Fernet = AES-128-CBC + HMAC-SHA256).
La clave vive en la variable de entorno SIGA_SEED_KEY (.env local y Variables
de Railway), nunca en git.

Contenido (JSON antes de comprimir):
    format       version del formato
    seed_id      sha256 del SQL normalizado (identifica el contenido, no la fecha)
    created_at   fecha de exportacion (UTC, ISO-8601)
    source       version de PostgreSQL de origen
    sections     {"pre_data": sql, "data": sql, "post_data": sql}
                 (secciones de pg_dump: tablas sin constraints -> datos -> PK/FK/indices)
    counts       {"tabla": filas} para verificar la restauracion
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict, Iterator, List, Tuple, Union

from cryptography.fernet import Fernet, InvalidToken

FORMAT_VERSION = 1
SECTIONS = ("pre_data", "data", "post_data")


class SeedError(RuntimeError):
    """Error de snapshot: clave invalida, archivo corrupto o restauracion inconsistente."""


# ---------------------------------------------------------------------------
# Cifrado
# ---------------------------------------------------------------------------
def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


def _fernet(key: str) -> Fernet:
    try:
        return Fernet(key.strip().encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise SeedError("SIGA_SEED_KEY con formato invalido (debe ser una clave Fernet de 44 caracteres).") from exc


def pack(payload: dict, key: str) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _fernet(key).encrypt(gzip.compress(raw, compresslevel=9, mtime=0))


def unpack(blob: bytes, key: str) -> dict:
    try:
        raw = gzip.decompress(_fernet(key).decrypt(blob))
    except InvalidToken as exc:
        raise SeedError("No se pudo descifrar el snapshot: SIGA_SEED_KEY no corresponde a este archivo.") from exc
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("format") != FORMAT_VERSION:
        raise SeedError(f"Formato de snapshot no soportado: {payload.get('format')}")
    return payload


# ---------------------------------------------------------------------------
# Normalizacion del SQL de pg_dump
# ---------------------------------------------------------------------------
# pg_dump >= 17.6 emite "\restrict <clave-aleatoria>" / "\unrestrict"; son
# meta-comandos de psql (no SQL) y cambiarian el seed_id en cada exportacion.
_META_LINE = re.compile(r"^\\(?!\.)")
# SET de sesion que entienden todas las versiones soportadas (>= 13). Otros
# (p.ej. transaction_timeout, solo PG >= 17) se omiten para restaurar en
# servidores mas antiguos que el de origen.
SAFE_SETTINGS = {
    "statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout",
    "client_encoding", "standard_conforming_strings", "check_function_bodies",
    "xmloption", "client_min_messages", "row_security", "default_tablespace",
    "default_table_access_method",
}
_CREATE_SCHEMA = re.compile(r"^CREATE SCHEMA (?!IF NOT EXISTS)(\S+;)$")
_SET_LINE = re.compile(r"^SET\s+(?:SESSION\s+)?([a-z_]+)\s*(?:=|TO)", re.IGNORECASE)


def normalize_sql(sql: str) -> str:
    """Quita meta-comandos de psql y SET no portables; normaliza saltos de linea."""
    out: List[str] = []
    for line in sql.replace("\r\n", "\n").split("\n"):
        if _META_LINE.match(line):
            continue
        m = _SET_LINE.match(line)
        if m and m.group(1).lower() not in SAFE_SETTINGS:
            continue
        # Toda BD nueva ya trae el esquema public: crearlo de nuevo abortaria la restauracion.
        if _CREATE_SCHEMA.match(line):
            line = _CREATE_SCHEMA.sub(r"CREATE SCHEMA IF NOT EXISTS \1", line)
        out.append(line)
    return "\n".join(out)


def compute_seed_id(sections: Dict[str, str]) -> str:
    h = hashlib.sha256()
    for name in SECTIONS:
        h.update(name.encode())
        h.update(b"\0")
        h.update(sections[name].encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Lectura de la seccion de datos (COPY ... FROM stdin)
# ---------------------------------------------------------------------------
# Identificador SQL: simple (tabla) o entre comillas ("Tabla Rara", con "" escapadas).
_IDENT = r'(?:"(?:[^"]|"")+"|[^\s".(]+)'
_COPY_HEAD = re.compile(
    rf"^COPY\s+(?P<table>{_IDENT}(?:\.{_IDENT})?)\s*(?:\(.*\))?\s+FROM\s+stdin;\s*$")
_IDENT_RE = re.compile(_IDENT)


@dataclass
class CopyBlock:
    statement: str   # "COPY public.t (a, b) FROM stdin;"
    table: str       # nombre sin esquema ni comillas
    rows: str        # filas en formato texto de COPY, cada una terminada en "\n"
    count: int


Statement = Union[str, CopyBlock]


def _bare_table(qualified: str) -> str:
    name = _IDENT_RE.findall(qualified)[-1]  # ultimo identificador = tabla (sin esquema)
    return name[1:-1].replace('""', '"') if name.startswith('"') else name


def iter_data_section(sql: str) -> Iterator[Statement]:
    """Separa la seccion de datos en sentencias SQL y bloques COPY.

    Dentro de un bloque COPY cada linea es una fila (los saltos de linea de los
    valores vienen escapados como \\n), y el bloque termina en una linea "\\.".
    """
    lines = sql.split("\n")
    i, buf = 0, []
    while i < len(lines):
        line = lines[i]
        m = _COPY_HEAD.match(line)
        if m and not buf:
            rows: List[str] = []
            i += 1
            while i < len(lines) and lines[i] != "\\.":
                rows.append(lines[i])
                i += 1
            if i >= len(lines):
                raise SeedError(f"Bloque COPY sin terminar para {m.group('table')}")
            yield CopyBlock(line, _bare_table(m.group("table")),
                            "".join(r + "\n" for r in rows), len(rows))
            i += 1
            continue
        stripped = line.strip()
        if stripped and not stripped.startswith("--") or buf:
            buf.append(line)
            if stripped.endswith(";"):
                yield "\n".join(buf)
                buf = []
        i += 1
    if buf and "".join(buf).strip():
        raise SeedError("Sentencia SQL incompleta al final de la seccion de datos")


def count_rows(sql: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for st in iter_data_section(sql):
        if isinstance(st, CopyBlock):
            counts[st.table] = counts.get(st.table, 0) + st.count
    return counts


def split_tables(counts: Dict[str, int]) -> Tuple[int, int]:
    return len(counts), sum(counts.values())
