;; output_flood: emits 64-byte chunks forever. The output bound must stop it.
(module
  (import "env" "emit" (func $emit (param i32 i32)))
  (memory (export "memory") 1)
  (func (export "run") (result i64)
    (loop $l
      (call $emit (i32.const 0) (i32.const 64))
      (br $l))
    (i64.const 0)))
