# Python Slow Cancellable Behavior Goblin

Sleeps in short ticks, logs progress, and installs a SIGTERM handler that writes
a best-effort cancelled result envelope.

```powershell
docker build -t goblin-example-behavior-python-slow-cancellable:local .
```
