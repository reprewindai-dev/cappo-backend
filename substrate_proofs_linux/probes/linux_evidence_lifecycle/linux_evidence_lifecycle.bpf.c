#include <uapi/linux/ptrace.h>
#include <linux/sched.h>
#include <linux/bpf.h>

struct lifecycle_event_t {
    u32 event_type; // 1 = rmdir, 2 = mark_victim, 3 = exec
    u32 pid;
    u64 cgroup_id;
    char comm[16];
};

BPF_RINGBUF_OUTPUT(events, 64);

// The cgroup we are monitoring
BPF_ARRAY(config, u64, 1);
// Stats array: 0=seen, 1=submitted, 2=reserve_failures
BPF_ARRAY(stats, u64, 3);

// tracepoint:cgroup:cgroup_rmdir
// format: REC->root, REC->id, REC->level, __get_str(path)
TRACEPOINT_PROBE(cgroup, cgroup_rmdir) {
    int key = 0;
    u64 *target_cg = config.lookup(&key);
    if (!target_cg) return 0;

    u64 rm_cg_id = args->id;
    if (rm_cg_id == *target_cg) {
        int stat_idx = 0;
        u64 *seen = stats.lookup(&stat_idx);
        if (seen) __sync_fetch_and_add(seen, 1);

        struct lifecycle_event_t *ev = events.ringbuf_reserve(sizeof(struct lifecycle_event_t));
        if (!ev) {
            stat_idx = 2;
            u64 *drops = stats.lookup(&stat_idx);
            if (drops) __sync_fetch_and_add(drops, 1);
            return 0;
        }

        ev->event_type = 1;
        ev->pid = bpf_get_current_pid_tgid() >> 32;
        ev->cgroup_id = rm_cg_id;
        bpf_get_current_comm(&ev->comm, sizeof(ev->comm));
        
        events.ringbuf_submit(ev, 0);
        stat_idx = 1;
        u64 *submitted = stats.lookup(&stat_idx);
        if (submitted) __sync_fetch_and_add(submitted, 1);
    }
    return 0;
}

// tracepoint:oom:mark_victim
// format: REC->pid, __get_str(comm), REC->total_vm, etc
TRACEPOINT_PROBE(oom, mark_victim) {
    int key = 0;
    u64 *target_cg = config.lookup(&key);
    if (!target_cg) return 0;

    int stat_idx = 0;
    u64 *seen = stats.lookup(&stat_idx);
    if (seen) __sync_fetch_and_add(seen, 1);

    u64 current_cg = bpf_get_current_cgroup_id();
    
    struct lifecycle_event_t *ev = events.ringbuf_reserve(sizeof(struct lifecycle_event_t));
    if (!ev) {
        stat_idx = 2;
        u64 *drops = stats.lookup(&stat_idx);
        if (drops) __sync_fetch_and_add(drops, 1);
        return 0;
    }

    ev->event_type = 2;
    ev->pid = args->pid; // The victim PID
    ev->cgroup_id = current_cg;
    
    __builtin_memset(&ev->comm, 0, sizeof(ev->comm));
    
    events.ringbuf_submit(ev, 0);
    stat_idx = 1;
    u64 *submitted = stats.lookup(&stat_idx);
    if (submitted) __sync_fetch_and_add(submitted, 1);
    
    return 0;
}

// tracepoint:sched:sched_process_exec
TRACEPOINT_PROBE(sched, sched_process_exec) {
    int key = 0;
    u64 *target_cg = config.lookup(&key);
    if (!target_cg) return 0;

    u64 current_cg = bpf_get_current_cgroup_id();
    if (current_cg != *target_cg) return 0;

    int stat_idx = 0;
    u64 *seen = stats.lookup(&stat_idx);
    if (seen) __sync_fetch_and_add(seen, 1);

    struct lifecycle_event_t *ev = events.ringbuf_reserve(sizeof(struct lifecycle_event_t));
    if (!ev) {
        stat_idx = 2;
        u64 *drops = stats.lookup(&stat_idx);
        if (drops) __sync_fetch_and_add(drops, 1);
        return 0;
    }

    ev->event_type = 3;
    ev->pid = bpf_get_current_pid_tgid() >> 32;
    ev->cgroup_id = current_cg;
    bpf_get_current_comm(&ev->comm, sizeof(ev->comm));
    
    events.ringbuf_submit(ev, 0);
    stat_idx = 1;
    u64 *submitted = stats.lookup(&stat_idx);
    if (submitted) __sync_fetch_and_add(submitted, 1);

    return 0;
}
