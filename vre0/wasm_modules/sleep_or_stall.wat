;; sleep_or_stall.wat — FIXED for deadline scenario
;;
;; DESIGN: Uses an imported host function "host_sleep" that blocks in Python.
;; This consumes essentially zero additional wasm fuel while the Python host
;; sleeps for longer than the wall-clock deadline.
;;
;; Why not a spin loop: A spin loop consumes fuel, so fuel exhaustion would fire
;; before the wall-clock deadline. The imported sleep function lets the supervisor's
;; outer deadline be the sole termination discriminator.
;;
;; The host wrapper must provide the import:
;;   (import "env" "host_sleep" (func $host_sleep))
(module
  (import "env" "host_sleep" (func $host_sleep))
  (func (export "run")
    call $host_sleep
  )
)
