import sqlite3
from pathlib import Path

db = Path(r"C:\Users\antho\.n8n\database.sqlite")
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
cur = con.cursor()

# Get latest execution
rows = cur.execute("SELECT id, status FROM execution_entity ORDER BY id DESC LIMIT 1").fetchall()
for r in rows:
    print("Execution ID:", r[0])
    print("Status:", r[1])
    
# check execution_data or similar table if it exists
tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
if "execution_data" in tables:
    data_rows = cur.execute(f"SELECT * FROM execution_data WHERE executionId = {r[0]}").fetchall()
    print("Execution Data:", repr(data_rows)[:2000])

con.close()
