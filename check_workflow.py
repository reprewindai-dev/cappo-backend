import sqlite3, json
from pathlib import Path

db = Path(r"C:\Users\antho\.n8n\database.sqlite")
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
cur = con.cursor()

# Check workflow_entity table for the governed workflow
rows = cur.execute("SELECT id, name, active, versionId FROM workflow_entity WHERE id = 'W6r3rV1OqR'").fetchall()
print("workflow_entity rows:", rows)

# Check webhook_entity table
wh = cur.execute("SELECT * FROM webhook_entity WHERE workflowId = 'W6r3rV1OqR'").fetchall()
print("webhook_entity rows:", wh)

# Check if there is an active flag column
cols = [r[1] for r in cur.execute("PRAGMA table_info(workflow_entity)").fetchall()]
print("workflow_entity columns:", cols)

con.close()
