;; normal_ok.wat
;; Normal execution: returns a small computed value within all bounds.
;; Demonstrates that the governance envelope does not interfere with
;; valid workloads.
(module
  (func (export "run") (result i32)
    ;; Fibonacci(20) = 6765, computed iteratively
    (local $a i32)
    (local $b i32)
    (local $tmp i32)
    (local $i i32)
    (local.set $a (i32.const 0))
    (local.set $b (i32.const 1))
    (local.set $i (i32.const 0))
    (loop $fib
      (local.set $tmp (i32.add (local.get $a) (local.get $b)))
      (local.set $a (local.get $b))
      (local.set $b (local.get $tmp))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $fib (i32.lt_u (local.get $i) (i32.const 20)))
    )
    (local.get $b)
  )
)
