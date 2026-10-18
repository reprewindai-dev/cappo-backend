bpftrace -e 'tracepoint:oom:mark_victim { printf("victim %d\n", args->pid); }' > bpftrace.out 2>&1 &
BPF_PID=$!
sleep 2
cd /mnt/c/Users/antho/.gemini/antigravity/scratch/linux-vre-probes/probes/linux_evidence_lifecycle
python3 linux_evidence_lifecycle.py
kill -9 $BPF_PID
cat bpftrace.out
