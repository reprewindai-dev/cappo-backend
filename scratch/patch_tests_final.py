import sqlite3
import threading
import concurrent.futures

def patch_tests():
    with open("tests/capability_mount/test_governed_counter.py", "r", encoding="utf-8") as f:
        content = f.read()

    new_imports = '''import sqlite3
import threading
import concurrent.futures
'''
    if "import sqlite3" not in content:
        content = content.replace("from pathlib import Path", "from pathlib import Path\n" + new_imports)

    hostile_tests = '''

def _get_db_state(db_path, workspace: str, resource: str):
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT value, version FROM counters WHERE workspace=? AND resource=?", (workspace, resource)).fetchone()
        if not row:
            return None
        return {"value": row[0], "version": row[1]}

def test_missing_workspace_fails(tmp_path) -> None:
    adapter = GovernedCounterAdapter(tmp_path)
    with pytest.raises(ValueError, match="missing_workspace_identity"):
        adapter.dispatch(counter_context("counter.read", workspace=None))

def test_workspace_isolation(tmp_path) -> None:
    adapter = GovernedCounterAdapter(tmp_path)
    
    # w1 increments demo-1
    w1_inc = adapter.dispatch(counter_context("counter.increment", workspace="w1", resource="demo-1"))
    assert w1_inc["value"] == 1
    
    # w2 reads demo-1, gets 0
    w2_read = adapter.dispatch(counter_context("counter.read", workspace="w2", resource="demo-1"))
    assert w2_read["value"] == 0
    
    # w2 increments demo-1, gets 1
    w2_inc = adapter.dispatch(counter_context("counter.increment", workspace="w2", resource="demo-1"))
    assert w2_inc["value"] == 1
    
    # Ensure physical state matches
    assert _get_db_state(adapter.db_path, "w1", "demo-1")["value"] == 1
    assert _get_db_state(adapter.db_path, "w2", "demo-1")["value"] == 1

def test_concurrent_increments_not_lost(tmp_path) -> None:
    adapter = GovernedCounterAdapter(tmp_path)
    adapter.dispatch(counter_context("counter.read", workspace="w1", resource="demo-1"))
    
    def inc():
        return adapter.dispatch(counter_context("counter.increment", workspace="w1", resource="demo-1"))
        
    iterations = 100
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(inc) for _ in range(iterations)]
        concurrent.futures.wait(futures)
        
    state = _get_db_state(adapter.db_path, "w1", "demo-1")
    assert state["value"] == iterations
    assert state["version"] == iterations
'''
    if "test_missing_workspace_fails" not in content:
        content = content + hostile_tests

    with open("tests/capability_mount/test_governed_counter.py", "w", encoding="utf-8") as f:
        f.write(content)

patch_tests()
