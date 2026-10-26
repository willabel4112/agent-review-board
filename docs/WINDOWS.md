# Windows setup

Use native Windows Python 3.11+ and PowerShell 5.1 or later. Git is needed to
clone the repository. WSL and extra Python packages are unnecessary. These
instructions use the Python launcher's `py -3`; check that it selects 3.11+:

```powershell
py -3 -c "import sys; print(sys.version)"
py -3 -X utf8 examples\demo.py
```

Open the printed local URL. The demo uses temporary synthetic data and does not
install anything. Ctrl-C stops it and removes that data.

## Register a document and run the server

From the repository directory:

```powershell
$env:RB_DATA_DIR = Join-Path $env:LOCALAPPDATA 'agent-review-board'
py -3 -X utf8 boardctl.py register 'C:\My documents\plan.md' --id plan --title 'Project plan'
py -3 -X utf8 server.py
```

Open `http://127.0.0.1:8798/d/plan`. Set the same `RB_DATA_DIR` in every agent/CLI
terminal that should work with this board. Source Markdown and append-only
sidecars stay beside the registered document; `RB_DATA_DIR` contains the local
registry, claims and snapshots. Chinese names and paths with spaces are supported.
Document IDs must be a single filename component, excluding Windows reserved
names such as `CON` and characters such as `/`, `\` and `:`.

In a second PowerShell terminal:

```powershell
$env:RB_DATA_DIR = Join-Path $env:LOCALAPPDATA 'agent-review-board'
.\bin\boardctl.cmd pending --doc plan --json
.\bin\boardctl.cmd reply plan c-0001 --file answer.md --summary 'The short conclusion'
```

The `.cmd` launcher uses `py -3`. To select an interpreter explicitly, set
`$env:RB_PYTHON` to its real executable path, or call that interpreter directly.
Use [the agent instructions](../AGENTS.md) for the full review workflow and
[usage](USAGE.md) for native/explicit session identity.

## Optional service after login

Stop a foreground server using the same port first. The installer validates the
implementation with isolated synthetic tests before changing its scheduled task.
Use the same data directory for every management action:

```powershell
$data = Join-Path $env:LOCALAPPDATA 'agent-review-board'
.\install.ps1 -Action install -DataDir $data
.\install.ps1 -Action status -DataDir $data
.\install.ps1 -Action stop -DataDir $data
.\install.ps1 -Action start -DataDir $data
# Optional interpreter/port selection when installing:
# .\install.ps1 -Action install -DataDir $data -Python 'C:\Python313\python.exe' -Port 8808
# Remove this task, retaining documents, registry, snapshots and logs:
# .\install.ps1 -Action uninstall -DataDir $data
```

For a custom port, also set `$env:RB_PORT` in CLI terminals so printed links use
that port.

If your shell blocks script execution, invoke the command with a process-scoped
policy, for example `powershell.exe -NoProfile -ExecutionPolicy Bypass -File
.\install.ps1 -Action install -DataDir $data`. The installer does not change the
machine's execution policy or elevate itself. Organization policy may still
prevent task creation; that failure is reported.

The task runs as the installing user, with limited privileges and no saved
password. It starts after that user logs in, allows battery operation and retries
failures. It does not promise service before login. Physical reboot and
sleep/resume behavior are not part of the release's native smoke test.

`<data>\windows-service.json` records the task's ownership, source, interpreter,
port and data directory. `stop` disables its triggers/restarts and stops its
action; `start` re-enables it. Repeated stop/uninstall actions are safe. Missing
or mismatched ownership metadata causes refusal. The installer does not adopt
foreign tasks or terminate other Python processes. Moving the source/data path
creates a different registration; uninstall the old one using its original paths.

Logs are UTF-8 under `<data>\logs\windows-server.log` and
`windows-service.log`. A reported running task means its process stayed running;
open the local URL to check the application. The server still binds to
`127.0.0.1`; the installer adds no firewall rules or remote access.

## Run checks without installing

```powershell
py -3 -X utf8 -B tests\test_lib.py
py -3 -X utf8 -B tests\test_explore.py
py -3 -X utf8 -B tests\test_public.py
py -3 -X utf8 -B tests\test_platform.py
py -3 -X utf8 -B tests\test_snapshot_boundary.py
node tests\test_navigation.js
node tests\test_batch.js
```

Node is only needed for JavaScript tests. All fixtures are synthetic. Native
platform tests cover concurrent UTF-8 appends, lock timeouts/release, safe process
checks, Unicode paths, the actual `.cmd` launcher, PowerShell parsing and mocked
task ownership/failure recovery, plus a real loopback comment/submit/CLI reply
cycle. See [release notes](../CHANGELOG.md) for changes and limitations.
