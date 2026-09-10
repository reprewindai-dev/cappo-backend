#include <uapi/linux/ptrace.h>
#include <linux/sched.h>
#include <linux/bpf.h>

struct event_t {
    u32 type; // 1=EXEC, 2=OOM, 3=TEARDOWN
    u32 pid;
    u64 cgroup_id;
    u64 timestamp_ns;
    char comm[16];
};

BPF_RINGBUF_OUTPUT(events, 64);
BPF_ARRAY(config, u64, 1); // target cgroup_id

TRACEPOINT_PROBE(sched, sched_process_exec) {
    int key = 0;
    u64 *target = config.lookup(&key);
    if (!target) return 0;
    if (bpf_get_current_cgroup_id() == *target) {
        struct event_t *ev = events.ringbuf_reserve(sizeof(*ev));
        if (!ev) return 0;
        ev->type = 1;
        ev->pid = bpf_get_current_pid_tgid() >> 32;
        ev->cgroup_id = *target;
        ev->timestamp_ns = bpf_ktime_get_ns();
        bpf_get_current_comm(&ev->comm, sizeof(ev->comm));
        events.ringbuf_submit(ev, 0);
    }
    return 0;
}

TRACEPOINT_PROBE(oom, mark_victim) {
    int key = 0;
    u64 *target = config.lookup(&key);
    if (!target) return 0;
    // OOM context can be different, so just log victim pid
    // In userspace we correlate victim pid or current cgroup
    u64 current_cg = bpf_get_current_cgroup_id();
    struct event_t *ev = events.ringbuf_reserve(sizeof(*ev));
    if (!ev) return 0;
    ev->type = 2;
    ev->pid = args->pid;
    ev->cgroup_id = current_cg;
    ev->timestamp_ns = bpf_ktime_get_ns();
    __builtin_memset(&ev->comm, 0, sizeof(ev->comm));
    events.ringbuf_submit(ev, 0);
    return 0;
}

TRACEPOINT_PROBE(cgroup, cgroup_rmdir) {
    int key = 0;
    u64 *target = config.lookup(&key);
    if (!target) return 0;
    if (args->id == *target) {
        struct event_t *ev = events.ringbuf_reserve(sizeof(*ev));
        if (!ev) return 0;
        ev->type = 3;
        ev->pid = bpf_get_current_pid_tgid() >> 32;
        ev->cgroup_id = args->id;
        ev->timestamp_ns = bpf_ktime_get_ns();
        bpf_get_current_comm(&ev->comm, sizeof(ev->comm));
        events.ringbuf_submit(ev, 0);
    }
    return 0;
}
