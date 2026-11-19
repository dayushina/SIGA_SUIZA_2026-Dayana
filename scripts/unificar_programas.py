"""Unifica el catalogo de programas de estudio con la numeracion de los estudiantes.

Problema: `programas_estudio.id` (Gestion Academica) y `estudiantes.programa_id`
(asignado por Admision, ver mod-admision/main.py -> program_mapping) usaban
numeraciones distintas, y faltaban carreras en programas_estudio.

Este script (idempotente, en UNA transaccion):
  1. Guarda un respaldo JSON de las filas afectadas.
  2. Elimina programas/planes basura autogenerados (codigo TMP-...) sin uso.
  3. Renumera cada programa existente al id oficial (segun su nombre),
     repuntando todas las tablas hijas.
  4. Crea las carreras oficiales que falten (sin malla; se completa luego
     importando su Excel MINEDU con scripts/importar_carreras.py).
  5. Corrige codigos conocidos mal importados y ajusta la secuencia de ids.

Uso (desde la raiz del repo):
    siga_backend\\venv\\Scripts\\python scripts\\unificar_programas.py --dry-run
    siga_backend\\venv\\Scripts\\python scripts\\unificar_programas.py
"""
import argparse
import json
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from db_env import connect_db  # noqa: E402

# id oficial -> (nombre, codigo CNOF). Debe coincidir con
# siga_frontend/src/core/constants/programas.js y con el mapeo de Admision.
CATALOGO = {
    1: ("Desarrollo de Sistemas de Información", "J2662-3-001"),
    2: ("Enfermería Técnica", "Q3286-3-003"),
    4: ("Gestión Administrativa", "M2982-3-001"),
    5: ("Contabilidad", "M2969-3-001"),
    6: ("Mecatrónica Automotriz", "G2145-3-001"),
    7: ("Electricidad Industrial", "PEND-07"),
    8: ("Administración de Operaciones Turísticas", "PEND-08"),
    9: ("Construcción Civil", "PEND-09"),
    10: ("Producción Agropecuaria", "PEND-10"),
    11: ("Manejo Forestal", "PEND-11"),
}

# Tablas que referencian programas_estudio.id
HIJAS = [
    ("programa_configuraciones", "programa_id"),
    ("programa_modulos", "programa_id"),
    ("programa_requisitos", "programa_id"),
    ("carga_lectiva", "programa_id"),
    ("planes_trabajo_docente", "programa_id"),
    ("tutorias", "programa_id"),
    ("horarios_periodo", "programa_id"),
    ("perfiles_personal", "programa_estudio_id"),
]


def norm(texto):
    t = unicodedata.normalize("NFKD", texto or "")
    return "".join(ch for ch in t if not unicodedata.combining(ch)).upper().strip()


ID_POR_NOMBRE = {norm(n): i for i, (n, _) in CATALOGO.items()}


def filas(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def mover(cur, viejo, nuevo):
    """Cambia el id de un programa repuntando sus hijas (las FK no son ON UPDATE CASCADE)."""
    cur.execute(
        """INSERT INTO programas_estudio (id, nombre, codigo, descripcion, duracion_periodos,
               creditos_totales, plan_estudio_id, modalidad, estado, created_at, updated_at)
           SELECT %s, nombre, codigo || '#mv', descripcion, duracion_periodos, creditos_totales,
               plan_estudio_id, modalidad, estado, created_at, now()
           FROM programas_estudio WHERE id = %s""",
        (nuevo, viejo),
    )
    for tabla, col in HIJAS:
        cur.execute(f"UPDATE {tabla} SET {col} = %s WHERE {col} = %s", (nuevo, viejo))
    cur.execute("DELETE FROM programas_estudio WHERE id = %s", (viejo,))
    cur.execute("UPDATE programas_estudio SET codigo = replace(codigo, '#mv', '') WHERE id = %s", (nuevo,))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Mostrar cambios y deshacerlos")
    args = ap.parse_args()

    conn = connect_db()
    cur = conn.cursor()
    try:
        respaldo = {
            "programas_estudio": filas(cur, "SELECT * FROM programas_estudio ORDER BY id"),
            "hijas": {t: filas(cur, f"SELECT id, {c} FROM {t} WHERE {c} IS NOT NULL") for t, c in HIJAS},
        }
        destino = ROOT / f"backup_programas_{datetime.now():%Y%m%d_%H%M%S}.json"
        if not args.dry_run:
            destino.write_text(json.dumps(respaldo, default=str, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"Respaldo: {destino}")

        # 1. Basura autogenerada (solo si nada la usa)
        for p in filas(cur, "SELECT id, nombre FROM programas_estudio WHERE codigo LIKE 'TMP-%%'"):
            usos = 0
            for t, c in HIJAS:
                if t == "programa_modulos":
                    continue
                cur.execute(f"SELECT count(*) FROM {t} WHERE {c} = %s", (p["id"],))
                usos += cur.fetchone()[0]
            if usos:
                print(f"[AVISO] '{p['nombre']}' (id {p['id']}) tiene datos asociados; no se elimina")
                continue
            cur.execute("DELETE FROM programa_unidades WHERE programa_modulo_id IN "
                        "(SELECT id FROM programa_modulos WHERE programa_id = %s)", (p["id"],))
            cur.execute("DELETE FROM programa_modulos WHERE programa_id = %s", (p["id"],))
            cur.execute("DELETE FROM programas_estudio WHERE id = %s", (p["id"],))
            print(f"[BORRADO] programa basura '{p['nombre']}' (id {p['id']})")
        cur.execute("SELECT id FROM planes_estudio WHERE codigo LIKE 'TMP-%%' AND id NOT IN "
                    "(SELECT plan_estudio_id FROM programas_estudio WHERE plan_estudio_id IS NOT NULL)")
        for (plan_id,) in cur.fetchall():
            cur.execute("DELETE FROM unidades_plan WHERE modulo_id IN (SELECT id FROM modulos_plan WHERE plan_id = %s)", (plan_id,))
            cur.execute("DELETE FROM modulos_plan WHERE plan_id = %s", (plan_id,))
            cur.execute("DELETE FROM planes_estudio WHERE id = %s", (plan_id,))
            print(f"[BORRADO] plan basura id {plan_id}")

        # 2. Renumerar al id oficial (en dos fases para evitar choques de ids)
        movimientos = {}
        for p in filas(cur, "SELECT id, nombre FROM programas_estudio ORDER BY id"):
            oficial = ID_POR_NOMBRE.get(norm(p["nombre"]))
            if oficial is None:
                print(f"[AVISO] '{p['nombre']}' (id {p['id']}) no esta en el catalogo oficial; se deja igual")
            elif oficial != p["id"]:
                movimientos[p["id"]] = oficial
        for viejo in movimientos:
            mover(cur, viejo, viejo + 100000)
        for viejo, nuevo in movimientos.items():
            mover(cur, viejo + 100000, nuevo)
            print(f"[RENUMERADO] id {viejo} -> {nuevo} ({CATALOGO[nuevo][0]})")

        # 3. Nombres/codigos oficiales y carreras faltantes
        for pid, (nombre, codigo) in CATALOGO.items():
            cur.execute("SELECT codigo FROM programas_estudio WHERE id = %s", (pid,))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    """INSERT INTO programas_estudio (id, nombre, codigo, descripcion, duracion_periodos,
                           creditos_totales, modalidad, estado, created_at, updated_at)
                       VALUES (%s,%s,%s,'Pendiente: importar plan de estudios MINEDU',6,0,
                           'presencial','activo',now(),now())""",
                    (pid, nombre, codigo),
                )
                print(f"[CREADO] {pid}: {nombre}")
            else:
                cambio_codigo = not codigo.startswith("PEND-") and row[0] != codigo
                cur.execute(
                    "UPDATE programas_estudio SET nombre = %s, codigo = %s, updated_at = now() WHERE id = %s",
                    (nombre, codigo if cambio_codigo else row[0], pid),
                )
                if cambio_codigo:
                    print(f"[CODIGO] {nombre}: {row[0]} -> {codigo}")

        cur.execute("SELECT setval('programas_estudio_id_seq', (SELECT max(id) FROM programas_estudio))")

        for p in filas(cur, """SELECT p.id, p.codigo, p.nombre, count(m.id) AS ciclos FROM programas_estudio p
                               LEFT JOIN programa_modulos m ON m.programa_id = p.id GROUP BY p.id ORDER BY p.id"""):
            print(f"   {p['id']:>3}  {p['codigo']:<13} {p['nombre']}  ({p['ciclos']} ciclos)")

        if args.dry_run:
            conn.rollback()
            print("DRY-RUN: no se guardo nada.")
        else:
            conn.commit()
            print("Listo.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
