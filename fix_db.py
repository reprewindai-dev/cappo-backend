import sqlite3
conn = sqlite3.connect('cappo.db')
c = conn.cursor()
c.execute("UPDATE kms_key_records SET status='ACTIVE'")
conn.commit()
