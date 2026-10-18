from wasmtime import Linker, FuncType, Engine, Config, ValType, Store, Module, Instance
import time

cfg = Config()
cfg.consume_fuel = True
engine = Engine(cfg)
store = Store(engine)
store.set_fuel(1_000_000)

linker = Linker(engine)

def host_sleep():
    print("host_sleep called")
    time.sleep(0.001)

linker.define_func(
    "env",
    "host_sleep",
    FuncType([], []),
    host_sleep,
)

wat = b"""
(module
  (import "env" "host_sleep" (func $host_sleep))
  (func (export "run")
    call $host_sleep
  )
)
"""

module = Module(engine, wat)
instance = linker.instantiate(store, module)
run = instance.exports(store)["run"]
run(store)
print("success")
