import sqlite3
import os
import time

DB_PATH = "target_state.db"

def init_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE mutations (execution_id TEXT PRIMARY KEY, committed_at REAL)''')
    conn.commit()
    conn.close()

def mutate(execution_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        # Atomic insert, fails if duplicate
        c.execute("INSERT INTO mutations (execution_id, committed_at) VALUES (?, ?)", (execution_id, time.time()))
        conn.commit()
        return {"status": 200, "detail": "COMMITTED"}
    except sqlite3.IntegrityError:
        return {"status": 423, "detail": "LOCKED_DUPLICATE"}
    finally:
        conn.close()

def reconcile(execution_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT committed_at FROM mutations WHERE execution_id = ?", (execution_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {"status": 200, "detail": "RECONCILED_SUCCEEDED"}
    else:
        return {"status": 404, "detail": "RECONCILED_FAILED"}

def get_total_mutations():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM mutations")
    count = c.fetchone()[0]
    conn.close()
    return count
