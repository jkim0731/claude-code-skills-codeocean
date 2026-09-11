# Batch Runs & Job Tracking

```bash
# One run per session or subject (monitor by default)
./run_per_session.sh sessions.txt
./run_per_subject.sh subjects.txt

# Both write a tracking CSV; poll status with:
nohup ./track-jobs.sh tracking/sessions_<ts>.csv > track.log 2>&1 &

# Or check once:
./track-jobs.sh tracking/sessions_<ts>.csv

# Override poll interval (seconds):
POLL=600 ./track-jobs.sh tracking/sessions_<ts>.csv
```

Poll interval is auto-estimated from capsule run history (`/scratch/tmp/run-history.json`); defaults to 180s.

**Tracking CSV columns**: `item`, `computation_id`, `capsule_id`, `state`, `submitted_ts`
