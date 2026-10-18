;; normal_ok: touch 8 MiB of linear memory, return a deterministic checksum.
;; Fits well inside a 64 MiB boundary (runtime overhead ~34 MiB measured).
(module
  (memory (export "memory") 128)            ;; 128 pages = 8 MiB
  (func (export "run") (result i64)
    (local $p i32) (local $sum i64)
    (block $done
      (loop $l
        (i32.store8 (local.get $p) (i32.and (local.get $p) (i32.const 255)))
        (local.set $sum (i64.add (local.get $sum)
          (i64.extend_i32_u (i32.load8_u (local.get $p)))))
        (local.set $p (i32.add (local.get $p) (i32.const 4096)))
        (br_if $done (i32.ge_u (local.get $p) (i32.const 8388608)))
        (br $l)))
    (local.get $sum)))
