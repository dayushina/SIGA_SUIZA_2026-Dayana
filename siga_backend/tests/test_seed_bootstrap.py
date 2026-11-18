"""Snapshot cifrado de la BD (bootstrap/): formato, lectura de COPY, politica de
restauracion y endurecimiento de cuentas con contrasenas publicas."""
import os
from pathlib import Path

import bcrypt
import pytest

from bootstrap import restore_seed as rs
from bootstrap.seed_format import (
    CopyBlock, SeedError, compute_seed_id, count_rows, generate_key, iter_data_section,
    normalize_sql, pack, unpack,
)

SEED_DIR = Path(__file__).resolve().parent.parent / "bootstrap" / "seed"


# ---------------------------------------------------------------------------
# Cifrado
# ---------------------------------------------------------------------------
def test_pack_unpack_ida_y_vuelta():
    key = generate_key()
    payload = {"format": 1, "seed_id": "abc", "sections": {"data": "ñandú; \\N"}, "counts": {"t": 1}}
    blob = pack(payload, key)
    assert b"\xc3\xb1and" not in blob and b"abc" not in blob  # no queda texto en claro
    assert unpack(blob, key) == payload


def test_unpack_con_otra_clave_falla_con_mensaje_claro():
    blob = pack({"format": 1}, generate_key())
    with pytest.raises(SeedError, match="SIGA_SEED_KEY no corresponde"):
        unpack(blob, generate_key())


def test_clave_mal_formada():
    with pytest.raises(SeedError, match="formato invalido"):
        pack({"format": 1}, "no-es-una-clave")


def test_formato_desconocido():
    key = generate_key()
    with pytest.raises(SeedError, match="no soportado"):
        unpack(pack({"format": 99}, key), key)


# ---------------------------------------------------------------------------
# Normalizacion y lectura de la seccion de datos
# ---------------------------------------------------------------------------
def test_normalize_quita_metacomandos_y_set_no_portables():
    sql = ("\\restrict XyZ\nSET statement_timeout = 0;\nSET transaction_timeout = 0;\r\n"
           "CREATE SCHEMA public;\nCREATE TABLE public.t (id int);\n\\unrestrict XyZ")
    assert normalize_sql(sql) == (
        "SET statement_timeout = 0;\nCREATE SCHEMA IF NOT EXISTS public;\nCREATE TABLE public.t (id int);")


def test_seed_id_estable_y_sensible_al_contenido():
    a = {"pre_data": "x", "data": "y", "post_data": "z"}
    assert compute_seed_id(a) == compute_seed_id(dict(a))
    assert compute_seed_id(a) != compute_seed_id({**a, "data": "y2"})
    # el limite entre secciones cuenta: "ab"+"" != "a"+"b"
    assert compute_seed_id({"pre_data": "ab", "data": "", "post_data": ""}) != \
        compute_seed_id({"pre_data": "a", "data": "b", "post_data": ""})


DATA = (
    "--\n-- Data for Name: t\n--\n\n"
    "SET client_encoding = 'UTF8';\n"
    'COPY public."Tabla Rara" (id, txt) FROM stdin;\n'
    "1\tuno; con punto y coma;\n"
    "2\t\\N\n"
    "3\tlinea\\ncon salto y \\\\ barra\n"
    "\\.\n\n"
    "COPY public.vacia (id) FROM stdin;\n"
    "\\.\n\n"
    "SELECT pg_catalog.setval('public.t_id_seq', 3, true);\n"
)


def test_iter_data_section_separa_copy_y_sentencias():
    items = list(iter_data_section(DATA))
    assert items[0] == "SET client_encoding = 'UTF8';"
    blk = items[1]
    assert isinstance(blk, CopyBlock) and blk.table == "Tabla Rara" and blk.count == 3
    assert blk.rows.endswith("barra\n") and "uno; con punto y coma;" in blk.rows
    assert isinstance(items[2], CopyBlock) and items[2].table == "vacia" and items[2].count == 0
    assert items[3].startswith("SELECT pg_catalog.setval")
    assert count_rows(DATA) == {"Tabla Rara": 3, "vacia": 0}


@pytest.mark.parametrize("head,tabla", [
    ('COPY public.estudiantes (id, nombres) FROM stdin;', "estudiantes"),
    ('COPY public."con.punto" (id) FROM stdin;', "con.punto"),
    ('COPY "Mi""Tabla" (id) FROM stdin;', 'Mi"Tabla'),
    ('COPY public.sin_columnas FROM stdin;', "sin_columnas"),
])
def test_nombres_de_tabla_en_copy(head, tabla):
    (blk,) = list(iter_data_section(head + "\n1\n\\.\n"))
    assert isinstance(blk, CopyBlock) and blk.table == tabla and blk.count == 1


def test_identificadores_escapados_en_consultas():
    assert rs.qident('Mi"Tabla') == '"Mi""Tabla"'


def test_copy_sin_terminar_es_error():
    with pytest.raises(SeedError, match="sin terminar"):
        list(iter_data_section("COPY public.t (id) FROM stdin;\n1\n"))


# ---------------------------------------------------------------------------
# Politica de restauracion
# ---------------------------------------------------------------------------
SEED = "a" * 64
OTRO = "b" * 64


def state(tables=0, applied=None, business=None, users=0):
    return rs.DbState(tables, applied, business or {}, users)


@pytest.mark.parametrize("mode,st,seed,accion", [
    ("auto", state(), SEED, "restore"),                                    # primer despliegue
    ("auto", state(38, SEED, {"estudiantes": 781}, 10), SEED, "none"),     # redeploy normal
    ("auto", state(38, SEED, {"estudiantes": 900}, 10), OTRO, "none"),     # snapshot nuevo: NO pisa produccion
    ("auto", state(38, None, {}, 1), SEED, "replace"),                     # BD de un arranque previo sin datos
    ("auto", state(38, None, {}, 2), SEED, "none"),                        # varios usuarios: hay datos reales
    ("auto", state(38, None, {"estudiantes": 5}, 1), SEED, "none"),        # datos de negocio: no se toca
    ("replace", state(38, SEED, {"estudiantes": 781}, 10), OTRO, "replace"),
    ("replace", state(38, SEED, {"estudiantes": 781}, 10), SEED, "none"),  # mismo snapshot: idempotente
    ("skip", state(), SEED, "none"),
    ("auto", state(), None, "none"),                                       # repo sin snapshot
])
def test_decide(mode, st, seed, accion):
    assert rs.decide(mode, st, seed)[0] == accion


def test_decide_explica_por_que_no_pisa_produccion():
    accion, motivo = rs.decide("auto", state(38, SEED, {"estudiantes": 900}, 10), OTRO)
    assert accion == "none" and "fuente de verdad" in motivo and "SIGA_SEED_MODE=replace" in motivo


@pytest.mark.parametrize("url,esperado", [
    ("postgresql+asyncpg://u:p@h:5432/db", "postgresql://u:p@h:5432/db"),
    ("postgres://u:p@h:5432/db", "postgresql://u:p@h:5432/db"),
    ("postgresql://u:p@h/db", "postgresql://u:p@h/db"),
])
def test_dsn_desde_database_url(url, esperado):
    assert rs.dsn_from_env({"DATABASE_URL": url}) == esperado


def test_dsn_sin_conexion_configurada():
    with pytest.raises(SeedError, match="DATABASE_URL"):
        rs.dsn_from_env({})


def test_modo_invalido():
    with pytest.raises(SeedError, match="SIGA_SEED_MODE invalido"):
        rs.run(env={"SIGA_SEED_MODE": "borrar-todo"})


# ---------------------------------------------------------------------------
# Endurecimiento de cuentas con contrasenas publicas
# ---------------------------------------------------------------------------
DEV = [
    {"email": "admin@siga.edu", "password": "admin123"},
    {"email": "docente@siga.edu", "password": "docente123"},
]


def _hash(pwd):
    return bcrypt.hashpw(pwd.encode(), bcrypt.gensalt(rounds=4)).decode()


class FakeCursor:
    def __init__(self, users):
        self.users = users          # {id: [email, hash, active]}
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if "to_regclass" in sql:
            self._result = [("core_users",)]
        elif sql.startswith("SELECT id"):
            self._result = [(i, e, h) for i, (e, h, a) in self.users.items() if a]
        elif "SET hashed_password" in sql:
            self.users[params[1]][1] = params[0]
        elif "SET is_active = false" in sql:
            self.users[params[0]][2] = False

    def fetchone(self):
        return self._result[0]

    def fetchall(self):
        return self._result


class FakeConn:
    def __init__(self, users):
        self.cur = FakeCursor(users)

    def cursor(self):
        return self.cur


def test_endurecimiento_reemplaza_admin_y_desactiva_cuentas_publicas():
    users = {
        1: ["admin@siga.edu", _hash("admin123"), True],
        2: ["docente@siga.edu", _hash("docente123"), True],
        3: ["gil@siga.edu", _hash("secreta-real"), True],          # cuenta real: intacta
        4: ["docente@siga.edu", _hash("ya-cambiada-2026"), True],  # ya no usa la publica: intacta
    }
    acciones = rs.harden_public_accounts(FakeConn(users), DEV, {"SIGA_ADMIN_PASSWORD": "Nueva-Clave-Segura-2026"})
    assert len(acciones) == 2
    assert bcrypt.checkpw(b"Nueva-Clave-Segura-2026", users[1][1].encode()) and users[1][2]
    assert users[2][2] is False
    assert users[3][2] and users[4][2]


def test_endurecimiento_exige_password_si_el_admin_es_publico():
    users = {1: ["admin@siga.edu", _hash("admin123"), True]}
    with pytest.raises(SeedError, match="SIGA_ADMIN_PASSWORD"):
        rs.harden_public_accounts(FakeConn(users), DEV, {})
    with pytest.raises(SeedError):
        rs.harden_public_accounts(FakeConn(users), DEV, {"SIGA_ADMIN_PASSWORD": "docente123"})


def test_endurecimiento_sin_cuentas_publicas_no_hace_nada():
    users = {1: ["admin@siga.edu", _hash("otra-clave-larga"), True]}
    assert rs.harden_public_accounts(FakeConn(users), DEV, {}) == []


def test_admin_personalizado_por_email():
    users = {1: ["admin@siga.edu", _hash("admin123"), True]}
    rs.harden_public_accounts(FakeConn(users), DEV, {"SIGA_ADMIN_EMAIL": "direccion@suiza.edu.pe"})
    assert users[1][2] is False  # no es el admin configurado: se desactiva


# ---------------------------------------------------------------------------
# Snapshot versionado en el repositorio
# ---------------------------------------------------------------------------
def test_snapshot_del_repo_es_consistente_con_su_manifiesto():
    enc, manifest = SEED_DIR / "siga_seed.enc", SEED_DIR / "siga_seed.manifest"
    if not enc.exists():
        pytest.skip("el repositorio no incluye snapshot")
    assert manifest.exists(), "falta siga_seed.manifest junto a siga_seed.enc"
    seed_id = manifest.read_text(encoding="utf-8").split()[0]
    assert len(seed_id) == 64
    key = os.environ.get("SIGA_SEED_KEY")
    if not key:
        pytest.skip("SIGA_SEED_KEY no definida: no se puede descifrar el snapshot")
    payload = unpack(enc.read_bytes(), key)
    assert payload["seed_id"] == seed_id == compute_seed_id(payload["sections"])
    assert payload["counts"] == count_rows(payload["sections"]["data"])
