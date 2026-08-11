# Private Grove deployment

These files are deployment templates; review paths before installing them.

1. Keep the exe.dev proxy private and point it only at nginx on
   `127.0.0.1:8000`. Confirm an anonymous request is redirected by exe.dev.
2. Install the Grove frontend in `/var/www/grove` and the application plus its
   virtual environment under `/opt/grove`.
3. Create the locked-down `grove` system user. Create `/etc/grove/grove.env` as
   root with mode `0600`; put provider credentials and all `BASEMODE_LOOM_*`
   production settings there. Do not put secrets in unit files or command-line
   arguments.
4. Install `nginx-grove.conf`, `grove.service`, and the backup service/timer in
   their corresponding nginx/systemd directories. Create `/var/backups/grove`
   owned by `grove`, mode `0700`.
5. Enable nginx, `grove.service`, and `grove-backup.timer`. Enable the host OS's
   automatic security-update facility (for Debian/Ubuntu, `unattended-upgrades`).
6. Configure provider-side spending limits and alerts independently of Grove.

Example environment file (values shown are not secrets):

```sh
BASEMODE_LOOM_ALLOWED_ORIGINS=https://grove.example.com,https://grove.example.exe.xyz
BASEMODE_LOOM_MAX_MESSAGE_BYTES=1048576
BASEMODE_LOOM_MAX_FIELD_BYTES=262144
BASEMODE_LOOM_MAX_CONTEXT_TOKENS=32768
BASEMODE_LOOM_CONCURRENT_GENERATION_JOBS=1
BASEMODE_LOOM_MAX_BRANCHES_PER_JOB=8
BASEMODE_LOOM_GENERATION_TIMEOUT_SECONDS=120
BASEMODE_LOOM_MAX_OUTPUT_TOKENS=2000
```

Before declaring the deployment complete, verify the acceptance criteria from
outside the VM: ports 8000 and 8010 must not be reachable directly, unrelated
HTTP and WebSocket origins must fail, reboot must restart Grove with its data,
and keys must be absent from HTML, responses, logs, process arguments, and unit
files. Restore-test a backup as well as checking that backup files exist.
