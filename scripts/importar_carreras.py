"""Importa a la BD todos los programas de estudio (Excel MINEDU) de una carpeta.

Replica el flujo del boton "Importar carrera" de Gestion Academica
(AcademicDashboard.handleConfirmImport): crea el plan en planes_estudio
(+ modulos_plan / unidades_plan) y el programa en programas_estudio
(+ programa_modulos / programa_unidades), usando el mismo parser del backend.

Es idempotente: si ya existe un programa con el mismo codigo, lo omite.

Uso (desde la raiz del repo):
    siga_backend\\venv\\Scripts\\python scripts\\importar_carreras.py carreras12345
    ... --dry-run   para solo mostrar lo que se importaria
"""
import argparse
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "siga_backend" / "modules" / "mod-planes-estudio"))

from db_env import connect_db  # noqa: E402
from excel_parser import ExcelMineduParser  # noqa: E402


def norm(texto):
    t = unicodedata.normalize("NFKD", texto or "")
    return "".join(ch for ch in t if not unicodedata.combining(ch)).upper().strip()


def buscar_programa(cur, data):
    """Busca el programa por codigo o, si no, por nombre (sin tildes/mayusculas)."""
    cur.execute("SELECT id, nombre FROM programas_estudio")
    for pid, nombre in cur.fetchall():
        if norm(nombre) == norm(data["nombre"]):
            return pid
    cur.execute("SELECT id FROM programas_estudio WHERE codigo = %s", (data["codigo"],))
    row = cur.fetchone()
    return row[0] if row else None


def importar(cur, data):
    now = datetime.now()
    programa_id = buscar_programa(cur, data)
    if programa_id is not None:
        cur.execute("SELECT count(*) FROM programa_modulos WHERE programa_id = %s", (programa_id,))
        if cur.fetchone()[0]:
            return "ya existe con su malla, omitido"

    # 1. Plan de estudios (reutiliza el existente si el codigo ya esta)
    cur.execute("SELECT id FROM planes_estudio WHERE codigo = %s", (data["codigo"],))
    row = cur.fetchone()
    if row:
        plan_id = row[0]
    else:
        cur.execute(
            """INSERT INTO planes_estudio (codigo, nombre, descripcion, nivel_formativo,
                   creditos_totales, horas_totales, estado, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (data["codigo"], data["nombre"], data["descripcion"], data["nivel_formativo"],
             data["creditos_totales"], data["horas_totales"], data["estado"], now, now),
        )
        plan_id = cur.fetchone()[0]
        for mod in data["modulos"]:
            cur.execute(
                """INSERT INTO modulos_plan (plan_id, codigo, nombre, horas, creditos, orden,
                       created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (plan_id, mod["codigo"], mod["nombre"], mod["horas"], mod["creditos"],
                 mod["orden"], now, now),
            )
            mod_id = cur.fetchone()[0]
            for ud in mod["unidades"]:
                cur.execute(
                    """INSERT INTO unidades_plan (modulo_id, codigo, nombre, horas, creditos,
                           tipo, orden, created_at, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (mod_id, ud["codigo"], ud["nombre"], ud["horas"], ud["creditos"],
                     ud["tipo"], ud["orden"], now, now),
                )

    # 2. Programa de estudios (lo que se lista en todos los selectores).
    #    Si ya existe sin malla (creado por unificar_programas.py), se completa
    #    conservando su id y su nombre oficial.
    if programa_id is not None:
        cur.execute(
            """UPDATE programas_estudio SET codigo = %s, descripcion = %s, creditos_totales = %s,
                   plan_estudio_id = %s, updated_at = %s WHERE id = %s""",
            (data["codigo"], data["descripcion"], data["creditos_totales"], plan_id, now, programa_id),
        )
    else:
        cur.execute(
            """INSERT INTO programas_estudio (nombre, codigo, descripcion, duracion_periodos,
                   creditos_totales, plan_estudio_id, modalidad, estado, created_at, updated_at)
               VALUES (%s,%s,%s,6,%s,%s,'presencial','activo',%s,%s) RETURNING id""",
            (data["nombre"], data["codigo"], data["descripcion"], data["creditos_totales"],
             plan_id, now, now),
        )
        programa_id = cur.fetchone()[0]

    # 3. Modulos (uno por ciclo) y unidades didacticas
    for i, mod in enumerate(data["modulos"]):
        orden = mod.get("orden") or i + 1
        cur.execute(
            """INSERT INTO programa_modulos (programa_id, nombre, periodo, horas_totales,
                   creditos, orden, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (programa_id, mod["nombre"], orden, mod["horas"], mod["creditos"], orden, now, now),
        )
        pm_id = cur.fetchone()[0]
        for j, ud in enumerate(mod["unidades"]):
            cur.execute(
                """INSERT INTO programa_unidades (programa_modulo_id, nombre, horas_teoria,
                       horas_practica, creditos, orden, created_at, updated_at)
                   VALUES (%s,%s,%s,0,%s,%s,%s,%s)""",
                (pm_id, ud["nombre"], ud["horas"], ud["creditos"], j + 1, now, now),
            )
    return f"importado (programa id={programa_id}, plan id={plan_id})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("carpeta", help="Carpeta con los Excel MINEDU (.xlsx)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    archivos = sorted(Path(args.carpeta).glob("*.xlsx"))
    if not archivos:
        sys.exit(f"No hay archivos .xlsx en {args.carpeta}")

    conn = connect_db()
    cur = conn.cursor()
    try:
        for f in archivos:
            ok, data, msg = ExcelMineduParser.parse_plan_estudio(f.read_bytes())
            if not ok or data["codigo"].startswith("TMP-"):
                print(f"[ERROR] {f.name}: {msg} (sin codigo de programa valido)")
                continue
            suma = sum(m["creditos"] for m in data["modulos"])
            resumen = f"{data['nombre']} ({data['codigo']}) - {len(data['modulos'])} ciclos, {suma} creditos"
            if args.dry_run:
                print(f"[DRY] {resumen}")
                continue
            print(f"[OK] {resumen}: {importar(cur, data)}")
        if not args.dry_run:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
