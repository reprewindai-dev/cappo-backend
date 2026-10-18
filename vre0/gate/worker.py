"""VRE-0 runtime shim: the only code that runs inside the execution boundary.

The supervisor places this process in its cgroup before exec. The shim then:
  1. compiles the guest module and sets the fuel budget,
  2. reports READY (module digest, fuel actually set) on ready_fd,
  3. blocks on gate_fd until the supervisor has checked the binding,
  4. runs the guest and writes a runtime report on report_fd.

Trust split: the shim is part of the runtime (TCB); the guest Wasm is
untrusted. The guest can reach only the two imports below. What it says
through `emit` streams straight to stdout, which the supervisor records as an
untrusted claim; it never decides the outcome.

If the kernel kills this process (OOM, deadline), no report is written; the
supervisor treats the missing report as evidence, not as an error to hide.

Exit codes: 0 returned, 3 fuel exhausted, 4 other trap, 5 output bound hit,
6 shim error.

Usage: worker.py <module.wat> <fuel> <output_max> <ready_fd> <gate_fd> <report_fd>
"""

import hashlib
import json
import os
import sys
import time


class OutputLimit(Exception):
    pass


def main() -> int:
    wat_path = sys.argv[1]
    fuel = int(sys.argv[2])
    output_max = int(sys.argv[3])
    ready_fd, gate_fd, report_fd = (int(a) for a in sys.argv[4:7])

    import wasmtime

    source = open(wat_path, "rb").read()
    cfg = wasmtime.Config()
    cfg.consume_fuel = True
    engine = wasmtime.Engine(cfg)
    store = wasmtime.Store(engine)
    store.set_fuel(fuel)
    module = wasmtime.Module(engine, source)

    emitted = 0

    def emit(caller, ptr, length):
        nonlocal emitted
        if emitted + length > output_max:
            raise OutputLimit()
        os.write(1, caller["memory"].read(caller, ptr, ptr + length))
        emitted += length

    def block():
        time.sleep(3600)

    linker = wasmtime.Linker(engine)
    linker.define_func("env", "emit",
                       wasmtime.FuncType([wasmtime.ValType.i32()] * 2, []),
                       emit, access_caller=True)
    linker.define_func("env", "block", wasmtime.FuncType([], []), block)
    run = linker.instantiate(store, module).exports(store)["run"]

    os.write(ready_fd, (json.dumps({
        "pid": os.getpid(),
        "module_sha256": hashlib.sha256(source).hexdigest(),
        "fuel_set": store.get_fuel(),
        "output_max": output_max,
    }) + "\n").encode())
    os.close(ready_fd)

    if os.read(gate_fd, 1) != b"G":
        return 6  # supervisor closed the gate without releasing it
    os.close(gate_fd)

    report = {}
    code = 6
    try:
        value = run(store)
        report.update(status="returned", return_value=str(value))
        code = 0
    except OutputLimit:
        report.update(status="trap", trap="output_limit")
        code = 5
    except wasmtime.Trap as trap:
        # wasmtime-py does not map the out-of-fuel trap code to its TrapCode
        # enum (reading .trap_code raises ValueError), so fuel exhaustion is
        # read from runtime state instead of from the message text.
        if store.get_fuel() == 0:
            report.update(status="trap", trap="fuel")
            code = 3
        else:
            report.update(status="trap", trap="other",
                          detail=trap.message.splitlines()[0][:200])
            code = 4
    report["fuel_remaining"] = store.get_fuel()
    report["fuel_consumed"] = fuel - store.get_fuel()
    report["output_bytes"] = emitted
    os.write(report_fd, json.dumps(report).encode())
    os.close(report_fd)
    return code


if __name__ == "__main__":
    sys.exit(main())
