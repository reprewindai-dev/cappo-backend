import sqlite3
from pathlib import Path

dbs = [
    Path(r"C:\Users\antho\.n8n\database.sqlite"),
    Path(r"C:\Users\antho\.n8n\database.sqlite.bak"),
    Path(r"C:\Users\antho\.n8n\.n8n\database.sqlite"),
]
needles = ["W6r3rV1OqR", "governed-webhook", "Governed Execution"]

for db in dbs:
    print()
    print("=" * 60)
    sz = db.stat().st_size if db.exists() else 0
    print(str(db), f"({sz} bytes)")
    if not db.exists() or sz == 0:
        continue
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cur = con.cursor()
        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        print("tables:", ", ".join(tables[:30]))
        for table in tables:
            try:
                rows = cur.execute(f'SELECT * FROM "{table}" LIMIT 1000').fetchall()
                for row in rows:
                    text = repr(row)
                    if any(n.lower() in text.lower() for n in needles):
                        print(f"  MATCH table={table}:", text[:800])
            except Exception:
                pass
        con.close()
    except Exception as e:
        print("ERROR:", e)
