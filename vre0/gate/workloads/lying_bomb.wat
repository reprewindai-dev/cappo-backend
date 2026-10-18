;; lying_bomb: first emits "COMPLETED ok" through the output channel (a false
;; self-report), then behaves like ram_bomb. The receipt must ignore the claim.
(module
  (import "env" "emit" (func $emit (param i32 i32)))
  (memory (export "memory") 1)
  (data (i32.const 0) "COMPLETED ok")
  (func (export "run") (result i64)
    (local $base i32) (local $p i32) (local $n i32)
    (call $emit (i32.const 0) (i32.const 12))
    (block $done
      (loop $grow
        (local.set $base (i32.mul (memory.size) (i32.const 65536)))
        (br_if $done (i32.eq (memory.grow (i32.const 16)) (i32.const -1)))
        (local.set $p (local.get $base))
        (block $touched
          (loop $touch
            (i32.store8 (local.get $p) (i32.const 1))
            (local.set $p (i32.add (local.get $p) (i32.const 4096)))
            (br_if $touched (i32.ge_u (local.get $p)
              (i32.add (local.get $base) (i32.const 1048576))))
            (br $touch)))
        (local.set $n (i32.add (local.get $n) (i32.const 1)))
        (br_if $done (i32.ge_u (local.get $n) (i32.const 192)))
        (br $grow)))
    (i64.extend_i32_u (local.get $n))))
