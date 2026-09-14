import sqlite3
import json
import re

def patch_effects_proper():
    with open("cappo_backend/capability_mount/effects.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # 1. Update ConsequenceContext
    context_old = '''@dataclass(frozen=True)
class ConsequenceContext:
    action: str
    resource: str
    arguments: Mapping[str, object]
    operation_id: str | None'''
    context_new = '''@dataclass(frozen=True)
class ConsequenceContext:
    action: str
    resource: str
    arguments: Mapping[str, object]
    operation_id: str | None
    workspace: str | None = None'''
    content = content.replace(context_old, context_new)
    
    # 2. Add sqlite3 import if not present
    if "import sqlite3" not in content:
        content = content.replace("import json", "import json\nimport sqlite3")
        
    # 3. Rewrite GovernedCounterAdapter
    adapter_new = '''class GovernedCounterAdapter(TargetAdapter):
    """Sandbox reference capability for one-step governed counter mutations."""

    ref = "activation.governed-counter"
    actions = frozenset({"counter.read", "counter.increment", "counter.reset"})

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.invocation_count = 0
        self.invocations_by_action: dict[str, int] = {}
        self.db_path = self.root / "governed_counters.db"
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path, timeout=15.0, isolation_level=None) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS counters (
                    workspace TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    value INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (workspace, resource)
                )
            """)

    def dispatch(self, context: ConsequenceContext) -> object:
        self.invocation_count += 1
        self.invocations_by_action[context.action] = (
            self.invocations_by_action.get(context.action, 0) + 1
        )
        
        validate_resource(context.resource)
        
        if not context.workspace:
            raise ValueError("missing_workspace_identity")

        with sqlite3.connect(self.db_path, timeout=15.0, isolation_level="IMMEDIATE") as conn:
            cursor = conn.cursor()
            
            # Ensure row exists
            cursor.execute("""
                INSERT OR IGNORE INTO counters (workspace, resource, value, version)
                VALUES (?, ?, 0, 0)
            """, (context.workspace, context.resource))
            
            if context.action == "counter.read":
                cursor.execute('SELECT value, version FROM counters WHERE workspace = ? AND resource = ?', 
                               (context.workspace, context.resource))
                row = cursor.fetchone()
                return {
                    "resource": context.resource,
                    "value": row[0],
                    "version": row[1],
                }

            if context.action == "counter.increment":
                cursor.execute('SELECT value, version FROM counters WHERE workspace = ? AND resource = ?', 
                               (context.workspace, context.resource))
                row = cursor.fetchone()
                previous_value = row[0]
                
                cursor.execute("""
                    UPDATE counters 
                    SET value = value + 1, version = version + 1 
                    WHERE workspace = ? AND resource = ?
                    RETURNING value, version
                """, (context.workspace, context.resource))
                row = cursor.fetchone()
                value = row[0]
                version = row[1]
                return {
                    "resource": context.resource,
                    "previous_value": previous_value,
                    "value": value,
                    "version": version,
                }

            if context.action == "counter.reset":
                cursor.execute("""
                    UPDATE counters 
                    SET value = 0, version = version + 1 
                    WHERE workspace = ? AND resource = ?
                    RETURNING value, version
                """, (context.workspace, context.resource))
                row = cursor.fetchone()
                return {
                    "resource": context.resource,
                    "value": 0,
                    "version": row[1],
                }

        raise ValueError("target_not_mapped")'''

    adapter_pattern = re.compile(r'class GovernedCounterAdapter\(TargetAdapter\):.*?raise ValueError\("target_not_mapped"\)', re.DOTALL)
    if not adapter_pattern.search(content):
        print("COULD NOT FIND REGEX MATCH!")
        return

    content = adapter_pattern.sub(adapter_new, content)
    
    with open("cappo_backend/capability_mount/effects.py", "w", encoding="utf-8") as f:
        f.write(content)
        
    print("Patched successfully")

patch_effects_proper()
