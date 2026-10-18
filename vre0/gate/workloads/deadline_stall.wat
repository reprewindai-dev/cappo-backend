;; deadline_stall: calls a host import that blocks (models a guest stuck on
;; I/O). Blocking consumes no fuel, so only the wall deadline can stop it.
(module
  (import "env" "block" (func $block))
  (func (export "run") (result i64)
    (call $block)
    (i64.const 0)))
