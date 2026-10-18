;; sibling: the bystander. Touches 16 MiB, then runs a fixed arithmetic loop
;; long enough to outlive the attacker (about 0.8 s on the reference host) and
;; returns a value fixed by a solo calibration run. A wrong value or a kill
;; means the sibling was affected.
(module
  (memory (export "memory") 256)            ;; 16 MiB
  (func (export "run") (result i64)
    (local $p i32) (local $i i64) (local $acc i64)
    (block $done
      (loop $l
        (i32.store8 (local.get $p) (i32.const 7))
        (local.set $p (i32.add (local.get $p) (i32.const 4096)))
        (br_if $done (i32.ge_u (local.get $p) (i32.const 16777216)))
        (br $l)))
    (local.set $acc (i64.const 1469598103934665603))
    (block $fin
      (loop $m
        (local.set $acc (i64.mul (i64.xor (local.get $acc) (local.get $i))
                                 (i64.const 1099511628211)))
        (local.set $i (i64.add (local.get $i) (i64.const 1)))
        (br_if $fin (i64.ge_u (local.get $i) (i64.const 500000000)))
        (br $m)))
    (local.get $acc)))
