import psycopg2
from db_env import connect_db  # credenciales desde .env
conn = connect_db()
cur = conn.cursor()
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='core_users' ORDER BY ordinal_position;")
cols = cur.fetchall()
print('Columnas core_users:', [c[0] for c in cols])
cur.close()
conn.close()