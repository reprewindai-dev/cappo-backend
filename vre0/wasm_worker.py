#!/usr/bin/env python3
import sys
import time
import pathlib
from wasmtime import Config, Engine, Store, Module, Instance, Linker, FuncType

def run():
    scenario = sys.argv[1]
    wat_path = pathlib.Path(sys.argv[2])
    fuel_budget = int(sys.argv[3])
    
    cfg = Config()
    cfg.consume_fuel = True
    engine = Engine(cfg)
    store = Store(engine)
    store.set_fuel(fuel_budget)
    
    if scenario == "deadline_stall":
        def _host_sleep() -> None:
            time.sleep(3)
            
        linker = Linker(engine)
        linker.define_func(
            "env",
            "host_sleep",
            FuncType([], []),
            lambda: _host_sleep(),
        )
        module = Module(engine, wat_path.read_bytes())
        instance = linker.instantiate(store, module)
        run_fn = instance.exports(store)["run"]
    else:
        module = Module(engine, wat_path.read_bytes())
        instance = Instance(store, module, [])
        run_fn = instance.exports(store)["run"]
        
    run_fn(store)

if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(1)
