#!/usr/bin/env python3
"""
Monitor Redis task_manager messages in real-time.
Traces the full lifecycle: Router init → status changes → cross-domain → accountant dispatch.

Usage: python3 scripts/monitor_task_manager.py
"""
import redis
import json
import time
from datetime import datetime

r = redis.Redis(host='localhost', port=6379, decode_responses=True)
pubsub = r.pubsub()

# Subscribe to all user channels
pubsub.psubscribe("user:*")

print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔍 Monitoring Redis PubSub — waiting for task_manager messages...")
print("=" * 100)

for message in pubsub.listen():
    if message['type'] not in ('pmessage',):
        continue

    channel = message.get('channel', '')
    try:
        data = json.loads(message.get('data', '{}'))
    except (json.JSONDecodeError, TypeError):
        continue

    ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]

    # Filter: show task_manager, notifications, and job dispatch
    if '/task_manager' in channel:
        job_id = data.get('job_id', '?')
        dept = data.get('department', '?')
        status = data.get('status', '?')
        task_type = data.get('type', '?')
        collection_id = data.get('collection_id', '?')

        # Shorten job_id for display
        jid_short = job_id[:35] + '...' if len(str(job_id)) > 35 else job_id

        print(f"\n[{ts}] 📋 TASK_MANAGER on {channel}")
        print(f"  type={task_type}  department={dept}  status={status}")
        print(f"  job_id={jid_short}  collection_id={collection_id}")

        # Show nested data keys
        nested = data.get('data', {})
        if isinstance(nested, dict):
            dept_data = nested.get('department_data', {})
            if dept_data:
                print(f"  department_data keys: {list(dept_data.keys())}")
            # Show key fields
            for k in ('status', 'department', 'file_name', 'routed_to', 'file_id', 'klk_job_id'):
                if k in nested:
                    print(f"  data.{k} = {nested[k]}")

    elif '/notifications' in channel:
        drive_id = data.get('drive_id', '?')
        status = data.get('status', data.get('update_data', {}).get('status', '?'))
        print(f"\n[{ts}] 🔔 NOTIFICATION on {channel}")
        print(f"  drive_id={drive_id}  status={status}")

    elif 'job_dispatch' in channel or 'agentic_dispatch' in channel:
        print(f"\n[{ts}] 🚀 JOB_DISPATCH on {channel}")
        print(f"  {json.dumps(data, default=str)[:200]}")
