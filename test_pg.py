import psycopg2
import sys

try:
    conn = psycopg2.connect("postgresql://test_user:rotated_sec_99_cappo@127.0.0.1:5432/cappo_test")
    cur = conn.cursor()
    cur.execute("SELECT 1;")
    print("Postgres OK")
    sys.exit(0)
except Exception as e:
    print("Postgres failed:", e)
    sys.exit(1)
