import psycopg2
from db_env import connect_db  # credenciales desde .env
conn = connect_db()
conn.autocommit = True
cur = conn.cursor()
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='periodos_academicos' ORDER BY ordinal_position;")
cols = cur.fetchall()
print('Columnas periodos_academicos:', [c[0] for c in cols])
cur.close()
conn.close()