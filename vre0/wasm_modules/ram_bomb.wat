;; ram_bomb.wat
;; Attack: allocates pages of memory in a loop until it can't anymore (OOM).
;; Does not rely on linear memory growth being intercepted by the engine.
;; Instead uses memory.grow in a loop; cgroup memory.max is the real enforcer.
(module
  (memory (export "memory") 1)
  (func (export "run")
    (local $i i32)
    (local $result i32)
    (local.set $i (i32.const 0))
    (block $break
      (loop $loop
        ;; Grow by 16 pages = 1 MiB per iteration
        (local.set $result (memory.grow (i32.const 16)))
        ;; Actually write to the newly allocated page to force RSS
        (i32.store
          (i32.mul (memory.size) (i32.const 65536))
          (local.get $i)
        )
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        ;; Grow up to 3000 pages (~192 MiB) – well beyond the 64 MiB cgroup limit
        (br_if $break (i32.ge_u (local.get $i) (i32.const 3000)))
        (br $loop)
      )
    )
  )
)
