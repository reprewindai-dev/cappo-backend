;; ram_bomb: grow linear memory 1 MiB at a time and write one byte into every
;; 4 KiB page of each new region, so the pages are really faulted in (RSS),
;; up to 192 MiB. The cgroup memory boundary is the intended enforcer.
(module
  (memory (export "memory") 1)
  (func (export "run") (result i64)
    (local $base i32) (local $p i32) (local $n i32)
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
