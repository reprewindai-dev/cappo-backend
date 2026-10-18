#include <uapi/linux/ptrace.h>
#include <linux/sched.h>
#include <linux/fs.h>

struct event_t {
    u64 timestamp_ns;
    u64 pid_tgid;
    u64 cgroup_id;
    u64 sequence;
    char comm[16];
    char filename[128];
};

// BCC template replacement for ring buffer size.
// RING_PAGES must be a power of 2.
BPF_RINGBUF_OUTPUT(events, RING_PAGES);

// Loss accounting counters
BPF_PERCPU_ARRAY(seen_total, u64, 1);
BPF_PERCPU_ARRAY(submitted_total, u64, 1);
BPF_PERCPU_ARRAY(reserve_failures, u64, 1);

// Configuration
BPF_ARRAY(config_cgroup_id, u64, 1);
BPF_ARRAY(sequence_counter, u64, 1);

TRACEPOINT_PROBE(sched, sched_process_exec) {
    int key = 0;
    
    // 1. Filter by target cgroup_id
    u64 *target_cgroup_id_ptr = config_cgroup_id.lookup(&key);
    if (!target_cgroup_id_ptr) return 0;
    
    u64 current_cgroup_id = bpf_get_current_cgroup_id();
    if (*target_cgroup_id_ptr != 0 && current_cgroup_id != *target_cgroup_id_ptr) {
        return 0; // Filtered out - not our target cgroup
    }

    // 2. Increment seen_total (we saw a matching event)
    u64 *seen = seen_total.lookup(&key);
    if (seen) {
        (*seen)++;
    }

    // 3. Reserve ring-buffer record
    struct event_t *event = events.ringbuf_reserve(sizeof(struct event_t));
    if (!event) {
        // Reserve failed -> increment drop counter and abort
        u64 *drops = reserve_failures.lookup(&key);
        if (drops) {
            (*drops)++;
        }
        return 0;
    }

    // 4. Populate event
    event->timestamp_ns = bpf_ktime_get_ns();
    event->pid_tgid = bpf_get_current_pid_tgid();
    event->cgroup_id = current_cgroup_id;
    
    // Populate sequence with 0 (optional)
    event->sequence = 0;

    // The new exec filename is available in comm at this point
    bpf_get_current_comm(&event->filename, sizeof(event->filename));

    // 5. Submit record to ring buffer
    events.ringbuf_submit(event, 0);

    // 6. Increment submitted_total
    u64 *submitted = submitted_total.lookup(&key);
    if (submitted) {
        (*submitted)++;
    }

    return 0;
}
