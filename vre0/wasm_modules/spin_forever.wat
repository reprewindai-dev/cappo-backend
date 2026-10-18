;; spin_forever.wat
;; Attack: tight infinite loop — consumes wasmtime fuel until exhaustion trap.
;; This demonstrates deterministic compute boundary via fuel mechanism.
(module
  (func (export "run")
    (loop $forever
      (br $forever)
    )
  )
)
