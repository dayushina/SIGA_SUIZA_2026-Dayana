import psycopg2
from db_env import connect_db  # credenciales desde .env
conn = connect_db()
cur = conn.cursor()
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='perfiles_personal' ORDER BY ordinal_position;")
cols = cur.fetchall()
print('Columnas perfiles_personal:')
for c in cols:
    print(f'  - {c[0]}')
cur.close()
conn.close()