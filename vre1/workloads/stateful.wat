;; stateful: a guest whose state lives in linear memory plus two globals.
;;
;; fill(pages, density_permille, seed)  grow to `pages` 64 KiB pages; each 4 KiB
;;                                      block becomes pseudo-random with
;;                                      probability density/1000, else stays zero.
;; step(n)                              n requests; each rewrites one random
;;                                      8-byte word in the first 64 KiB (hot set).
;; digest() -> i64                      FNV-1a over all memory, mixed with globals.
(module
  (memory (export "memory") 0)
  (global $seed (export "seed") (mut i64) (i64.const 1))
  (global $steps (export "steps") (mut i64) (i64.const 0))

  (func $rand (result i64)                      ;; xorshift64*
    (local $x i64)
    (local.set $x (global.get $seed))
    (local.set $x (i64.xor (local.get $x) (i64.shr_u (local.get $x) (i64.const 12))))
    (local.set $x (i64.xor (local.get $x) (i64.shl (local.get $x) (i64.const 25))))
    (local.set $x (i64.xor (local.get $x) (i64.shr_u (local.get $x) (i64.const 27))))
    (global.set $seed (local.get $x))
    (i64.mul (local.get $x) (i64.const 2685821657736338717)))

  (func (export "fill") (param $pages i32) (param $density i32) (param $s i64)
    (local $blk i32) (local $nblk i32) (local $p i32) (local $end i32)
    (global.set $seed (i64.or (local.get $s) (i64.const 1)))
    (drop (memory.grow (i32.sub (local.get $pages) (memory.size))))
    (local.set $nblk (i32.mul (local.get $pages) (i32.const 16)))
    (block $done
      (loop $blocks
        (br_if $done (i32.ge_u (local.get $blk) (local.get $nblk)))
        (if (i32.lt_u (i32.wrap_i64 (i64.rem_u (call $rand) (i64.const 1000))) (local.get $density))
          (then
            (local.set $p (i32.mul (local.get $blk) (i32.const 4096)))
            (local.set $end (i32.add (local.get $p) (i32.const 4096)))
            (block $bd
              (loop $words
                (br_if $bd (i32.ge_u (local.get $p) (local.get $end)))
                (i64.store (local.get $p) (call $rand))
                (local.set $p (i32.add (local.get $p) (i32.const 8)))
                (br $words)))))
        (local.set $blk (i32.add (local.get $blk) (i32.const 1)))
        (br $blocks))))

  (func (export "step") (param $n i32)
    (local $i i32) (local $off i32)
    (block $d
      (loop $l
        (br_if $d (i32.ge_u (local.get $i) (local.get $n)))
        (local.set $off (i32.and (i32.wrap_i64 (call $rand)) (i32.const 0xFFF8)))
        (i64.store (local.get $off) (i64.xor (i64.load (local.get $off)) (call $rand)))
        (global.set $steps (i64.add (global.get $steps) (i64.const 1)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l))))

  (func (export "digest") (result i64)
    (local $p i32) (local $end i32) (local $h i64)
    (local.set $h (i64.const -3750763034362895579))          ;; FNV offset basis
    (local.set $end (i32.mul (memory.size) (i32.const 65536)))
    (block $d
      (loop $l
        (br_if $d (i32.ge_u (local.get $p) (local.get $end)))
        (local.set $h (i64.mul (i64.xor (local.get $h) (i64.load (local.get $p)))
                               (i64.const 1099511628211)))
        (local.set $p (i32.add (local.get $p) (i32.const 8)))
        (br $l)))
    (i64.xor (local.get $h) (i64.xor (global.get $seed) (global.get $steps)))))
