;; spin_fuel: unbounded loop. Only the fuel budget (or the deadline) stops it.
(module
  (func (export "run") (result i64)
    (local $i i64)
    (loop $l
      (local.set $i (i64.add (local.get $i) (i64.const 1)))
      (br $l))
    (local.get $i)))
