# MCP call examples

```json
{"name": "coordinate", "arguments": {"target": "claude", "task": "Review docs/plan.md and list risks", "workspace": "/path/to/project", "mode": "consult", "route": "auto", "request_id": "plan-review-1"}}
```

```json
{"name": "job_wait", "arguments": {"job_id": "JOB_ID", "seconds": 45, "compact": true}}
```

```json
{"name": "coordinate_batch", "arguments": {"target": "qwen", "workspace": "/path", "tasks": ["task 1", "task 2"]}}
```

```json
{"name": "coordina_reply", "arguments": {"job_id": "JOB_ID", "reply_key": "KEY_FROM_TASK", "text": "Final answer", "response_kind": "final"}}
```
