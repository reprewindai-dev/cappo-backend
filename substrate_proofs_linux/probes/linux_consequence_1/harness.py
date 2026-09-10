import os
import sys
import time
import json
import sqlite3
import signal
import target_db

def worker_main(execution_id, crash_point):
    """
    Simulates the Linux-bound worker execution.
    crash_point:
      'BEFORE_DISPATCH': exit before mutating
      'AFTER_DISPATCH': mutate, then exit before acknowledging
      'NONE': normal run
    """
    if crash_point == "BEFORE_DISPATCH":
        sys.exit(9)
        
    res = target_db.mutate(execution_id)
    
    if crash_point == "AFTER_DISPATCH":
        sys.exit(9) # worker dies before it can return the receipt
        
    # Return receipt
    with open(f"receipt_{execution_id}.json", "w") as f:
        json.dump(res, f)
        
    sys.exit(0)

def run_test_lc1():
    target_db.init_db()
    results = []
    
    # LC1-A Normal finality
    exec_id_a = "exec-LC1-A"
    pid_a = os.fork()
    if pid_a == 0:
        worker_main(exec_id_a, "NONE")
    os.waitpid(pid_a, 0)
    
    with open(f"receipt_{exec_id_a}.json", "r") as f:
        receipt_a = json.load(f)
    
    recon_a = target_db.reconcile(exec_id_a)
    valid_a = (receipt_a["status"] == 200 and recon_a["status"] == 200)
    results.append({"Proof": "LC1-A", "Experiment": "Normal finality", "Result": valid_a})
    
    # LC1-B Crash-before-dispatch
    exec_id_b = "exec-LC1-B"
    pid_b = os.fork()
    if pid_b == 0:
        worker_main(exec_id_b, "BEFORE_DISPATCH")
    os.waitpid(pid_b, 0)
    
    recon_b = target_db.reconcile(exec_id_b)
    valid_b = (recon_b["status"] == 404)
    results.append({"Proof": "LC1-B", "Experiment": "Crash-before-dispatch", "Result": valid_b})
    
    # LC1-C Crash-after-dispatch (Target commits, executor loses confirmation)
    exec_id_c = "exec-LC1-C"
    pid_c = os.fork()
    if pid_c == 0:
        worker_main(exec_id_c, "AFTER_DISPATCH")
    os.waitpid(pid_c, 0)
    
    # Observe from harness (OUTCOME_UNKNOWN)
    has_receipt_c = os.path.exists(f"receipt_{exec_id_c}.json")
    
    # LC1-D Independent reconciliation
    recon_c = target_db.reconcile(exec_id_c)
    
    valid_c_d = (not has_receipt_c and recon_c["status"] == 200)
    results.append({"Proof": "LC1-C/D", "Experiment": "Crash-after-dispatch & Recon", "Result": valid_c_d})
    
    # LC1-E Duplicate suppression (attempt redispatch on same ID)
    res_e = target_db.mutate(exec_id_c)
    valid_e = (res_e["status"] == 423) # LOCKED
    results.append({"Proof": "LC1-E", "Experiment": "Duplicate suppression", "Result": valid_e})
    
    # Count total mutations to prove exactly ONE target consequence per valid ID
    total = target_db.get_total_mutations()
    # A (committed), B (not committed), C (committed) -> Total should be exactly 2
    valid_total = (total == 2)
    
    # Output report
    report = {
        "milestone": "LINUX-CONSEQUENCE-1",
        "verdict": "VALID" if all(r["Result"] for r in results) and valid_total else "INVALID",
        "battery": results,
        "total_mutations_enforced": total
    }
    
    print(json.dumps(report, indent=2))
    with open("consequence_battery_report.json", "w") as f:
        json.dump(report, f, indent=2)

if __name__ == "__main__":
    run_test_lc1()
