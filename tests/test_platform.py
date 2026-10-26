#!/usr/bin/env python3
"""Synthetic OS contracts; run on native Windows with py -3 -X utf8 -B tests/test_platform.py.

No production registry or sidecars are loaded. Multiprocessing always uses spawn,
so Windows and POSIX exercise the same independent-interpreter workers.
"""
from concurrent.futures import ThreadPoolExecutor
import errno
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote
from urllib.request import Request, build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import board_lib as lib
import board_platform as platform


def _append_worker(path, worker, count):
    def append(index):
        lib.append_event(path, {"worker": worker, "index": index,
                                "text": "中文 / spaces / 🧪" * 1024}, timeout=15)
        # Concurrent readers must cooperate with Windows mandatory byte locks.
        assert all("worker" in event for event in lib.read_events(path))
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(append, range(count)))


def _hold_lock(path, connection):
    with open(path, "ab", buffering=0) as file:
        with platform.file_lock(file):
            connection.send("locked")
            connection.recv()
    connection.close()


def _kernel_probe(path, connection):
    """Bypass our thread guard: test compatibility and coverage of the OS lock."""
    with open(path, "ab", buffering=0) as file:
        if os.name == "nt":
            overlap = platform._OVERLAPPED()
            overlap.Offset = 123
            overlap.OffsetHigh = 1  # >4GiB, beyond EOF; no large file allocation
            handle = platform.msvcrt.get_osfhandle(file.fileno())
            acquired = bool(platform._kernel.LockFileEx(handle, 3, 0, 1, 0,
                                                        platform.ctypes.byref(overlap)))
            if not acquired and platform.ctypes.get_last_error() != 33:
                raise platform.ctypes.WinError(platform.ctypes.get_last_error())
            if acquired:
                if not platform._kernel.UnlockFileEx(handle, 0, 1, 0,
                                                     platform.ctypes.byref(overlap)):
                    raise platform.ctypes.WinError(platform.ctypes.get_last_error())
        else:
            import fcntl
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                fcntl.flock(file, fcntl.LOCK_UN)
            except BlockingIOError:
                acquired = False
        connection.send(acquired)
    connection.close()


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="review-board-中文 空格-")
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "合成 events.jsonl"
        self.context = multiprocessing.get_context("spawn")
        self.processes = []

    def tearDown(self):
        for proc in self.processes:
            if proc.is_alive():
                proc.terminate()
            proc.join(10)
            proc.close()
        self.temp.cleanup()

    def spawn(self, target, *args):
        proc = self.context.Process(target=target, args=args)
        proc.start()
        self.processes.append(proc)
        return proc

    def holder(self):
        parent, child = self.context.Pipe()
        proc = self.spawn(_hold_lock, str(self.path), child)
        child.close()
        self.assertTrue(parent.poll(15), "holder did not acquire the lock")
        self.assertEqual(parent.recv(), "locked")
        return proc, parent

    def probe(self):
        parent, child = self.context.Pipe()
        proc = self.spawn(_kernel_probe, str(self.path), child)
        child.close()
        try:
            self.assertTrue(parent.poll(15), "kernel probe did not finish")
            result = parent.recv()
        finally:
            parent.close()
        proc.join(15)
        self.assertEqual(proc.exitcode, 0)
        return result

    def test_spawned_processes_and_threads_append_complete_utf8_lines(self):
        procs = [self.spawn(_append_worker, str(self.path), worker, 12) for worker in range(4)]
        for proc in procs:
            proc.join(30)
            self.assertEqual(proc.exitcode, 0)
        raw = self.path.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertNotIn(b"\r\n", raw)
        events = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
        self.assertEqual(len(events), 48)
        self.assertEqual({(e["worker"], e["index"]) for e in events},
                         {(worker, index) for worker in range(4) for index in range(12)})
        self.assertTrue(all(e["text"] == "中文 / spaces / 🧪" * 1024 for e in events))

    def test_timeout_does_not_append_and_release_allows_append(self):
        proc, connection = self.holder()  # starts with an empty file
        try:
            start = time.monotonic()
            with self.assertRaises(TimeoutError):
                lib.append_event(self.path, {"blocked": True}, timeout=0.15)
            self.assertGreaterEqual(time.monotonic() - start, 0.1)
            self.assertLess(time.monotonic() - start, 3)
            self.assertEqual(self.path.stat().st_size, 0)
            connection.send("release")
        finally:
            connection.close()
        proc.join(15)
        self.assertEqual(proc.exitcode, 0)
        lib.append_event(self.path, {"after": "释放"}, timeout=1)
        self.assertEqual(lib.read_events(self.path), [{"after": "释放"}])

    def test_os_lock_legacy_compatibility_and_beyond_eof(self):
        with open(self.path, "ab", buffering=0) as file:
            with platform.file_lock(file):
                self.assertFalse(self.probe())
            self.assertTrue(self.probe())

    def test_exception_unlocks_before_handle_closes(self):
        with open(self.path, "ab", buffering=0) as file:
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                with platform.file_lock(file):
                    raise RuntimeError("synthetic")
            self.assertTrue(self.probe())  # original file handle remains open
        self.assertFalse(platform._thread_locks)

    def test_thread_timeout_and_cleanup_with_same_open_handle(self):
        with open(self.path, "ab", buffering=0) as file:
            with platform.file_lock(file):
                def attempt():
                    with self.assertRaises(TimeoutError):
                        with platform.file_lock(file, timeout=0.1):
                            self.fail("concurrent thread acquired the same handle")
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(attempt).result(5)
        self.assertFalse(platform._thread_locks)

    def test_process_death_releases_os_lock(self):
        proc, connection = self.holder()
        proc.terminate()  # only the synthetic child created by this test
        proc.join(15)
        connection.close()
        lib.append_event(self.path, {"after": "process death"}, timeout=5)
        self.assertEqual(lib.read_events(self.path), [{"after": "process death"}])

    def test_lock_api_errors_are_not_swallowed(self):
        if os.name == "nt":
            api_patch = patch.object(platform._kernel, "LockFileEx", return_value=0)
            error_patch = patch.object(platform.ctypes, "get_last_error", return_value=6)
        else:
            api_patch = patch.object(platform.fcntl, "flock", side_effect=OSError(errno.EIO, "synthetic"))
            error_patch = patch.object(platform.time, "monotonic", wraps=time.monotonic)
        with api_patch, error_patch:
            with self.assertRaises(OSError):
                lib.append_event(self.path, {"must": "not write"})
        self.assertEqual(self.path.read_bytes(), b"")
        lib.append_event(self.path, {"after": "API error"})
        self.assertFalse(platform._thread_locks)

    def test_pid_probe_does_not_terminate_child(self):
        code = "import sys; print('ready', flush=True); sys.stdin.readline()"
        proc = subprocess.Popen([sys.executable, "-X", "utf8", "-c", code],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            self.assertEqual(proc.stdout.readline(), b"ready\n" if os.name != "nt" else b"ready\r\n")
            if os.name == "nt":
                with patch.object(platform.os, "kill", side_effect=AssertionError("Windows must not signal")):
                    self.assertTrue(all(lib.pid_alive(proc.pid) for _ in range(8)))
            else:
                self.assertTrue(all(lib.pid_alive(proc.pid) for _ in range(8)))
            self.assertIsNone(proc.poll())
            proc.communicate(b"exit\n", timeout=10)
            self.assertEqual(proc.returncode, 0)
            self.assertFalse(lib.pid_alive(proc.pid))
            for invalid in (None, "bad", -1, 0, 2**64):
                self.assertFalse(lib.pid_alive(invalid))
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.communicate(timeout=10)

    def test_windows_sharing_retry_is_bounded_and_preserves_failure(self):
        error = PermissionError("synthetic sharing violation")
        error.winerror = 32
        with patch.object(platform.os, "name", "nt"):
            with patch.object(platform.os, "replace", side_effect=[error, None]) as replace:
                platform.replace_file("synthetic-source", "synthetic-dest", timeout=1)
                self.assertEqual(replace.call_count, 2)
            with patch.object(platform.os, "replace", side_effect=error):
                with self.assertRaises(PermissionError):
                    platform.replace_file("synthetic-source", "synthetic-dest", timeout=0)

    def test_readers_wait_for_writer(self):
        lib.append_event(self.path, {"before": "writer"})
        proc, connection = self.holder()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(lib.read_events, str(self.path))
                time.sleep(0.1)
                self.assertFalse(future.done())
                connection.send("release")
                self.assertEqual(future.result(10), [{"before": "writer"}])
        finally:
            connection.close()
        proc.join(15)
        self.assertEqual(proc.exitcode, 0)

    def test_state_is_lazy_isolated_and_root_remains_source(self):
        state = self.root / "全新 state" / "nested"
        env = dict(os.environ, RB_DATA_DIR=str(state))
        code = """import json, os, board_lib as b
assert not os.path.exists(b.DATA_ROOT)
assert b.load_registry() == {} and b.load_claims() == {}
assert not os.path.exists(b.DATA_ROOT)
assert b.ROOT != b.DATA_ROOT
b.save_registry({'fixture': {'path': '中文 path.md'}})
b.save_claims({'fixture': {'pid': os.getpid()}})
sha = b.snapshot_put('fixture', '# 合成快照\\n')
assert b.snapshot_get('fixture', sha) == '# 合成快照\\n'
assert b.load_registry()['fixture']['path'] == '中文 path.md'
assert b.load_claims()['fixture']['pid'] == os.getpid()
print(json.dumps([b.ROOT, b.DATA_ROOT]))
"""
        result = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", code], cwd=ROOT,
                                env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [str(ROOT), str(state)])
        self.assertEqual({p.name for p in state.iterdir()},
                         {"registry.local.json", "claims.local.json", ".snapshots"})
        env.pop("RB_DATA_DIR")
        default = subprocess.run([sys.executable, "-B", "-c",
                                  "import board_lib as b; assert b.DATA_ROOT == b.ROOT"],
                                 cwd=ROOT, env=env, capture_output=True, timeout=10)
        self.assertEqual(default.returncode, 0, default.stderr)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="review-board-cli-中文 空格-")
        # Windows runners may give TEMP an 8.3 alias; the CLI prints realpath.
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "源码 source"
        self.source.mkdir()
        for name in ("board_lib.py", "boardctl.py", "board_platform.py", "server.py"):
            shutil.copyfile(ROOT / name, self.source / name)
        shutil.copyfile(ROOT / "install.ps1", self.source / "install.ps1")
        (self.source / "bin").mkdir()
        shutil.copyfile(ROOT / "bin" / "boardctl.cmd", self.source / "bin" / "boardctl.cmd")
        self.state = self.root / "状态 state"
        self.doc = self.root / "文档 files" / "合成 评审.md"
        self.doc.parent.mkdir()
        self.doc.write_text("# 合成标题\n\n需要解释的内容。\n", encoding="utf-8", newline="\n")
        self.env = dict(os.environ, RB_DATA_DIR=str(self.state), RB_PYTHON=sys.executable,
                        PYTHONIOENCODING="utf-8",
                        CODEX_THREAD_ID="synthetic-codex-thread", RB_AGENT_NAME="codex")
        # Synthetic tests never consult an active harness session registry.
        self.env.pop("RB_AGENT_SESSION", None)
        self.env.pop("CLAUDE_CODE_SESSION_ID", None)
        self.env.pop("RB_PORT", None)
        self.server = None
        self.url = None
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        if self.server is not None:
            self.server.terminate()
            self.server.communicate(timeout=10)
        self.temp.cleanup()

    def cli(self, *args, use_cmd=False):
        command = [sys.executable, "-X", "utf8", "-B", str(self.source / "boardctl.py"), *args]
        if use_cmd:
            command = subprocess.list2cmdline([str(self.source / "bin" / "boardctl.cmd"), *args])
        result = subprocess.run(command, env=self.env, cwd=self.root, capture_output=True,
                                text=True, encoding="utf-8", timeout=15, shell=use_cmd)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result.stdout

    def request(self, path, body=None):
        if self.url is None:
            # Same real Handler and IO, isolated interpreter; no listening socket.
            code = """import io, json, sys
from server import Handler
path, body = json.load(sys.stdin)
handler = object.__new__(Handler)
handler.path = path
result = []
handler._send = lambda status, payload, *args: result.append([status, payload])
if body is None:
    handler.do_GET()
else:
    data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    handler.headers = {'Content-Length': str(len(data))}
    handler.rfile = io.BytesIO(data)
    handler.do_POST()
print(json.dumps(result[0], ensure_ascii=False))
"""
            result = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", code],
                                    input=json.dumps([path, body], ensure_ascii=False),
                                    env=self.env, cwd=self.source, capture_output=True,
                                    text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            status, payload = json.loads(result.stdout)
            self.assertEqual(status, 200, payload)
            return payload
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(self.url + path, data=data,
                          headers={"Content-Type": "application/json"} if data else {})
        with self.opener.open(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_unicode_cli_registration_and_local_links(self):
        # Isolated state must never import even a source-local legacy registry.
        (self.source / "registry.json").write_text('{"legacy-sentinel": {}}', encoding="utf-8")
        output = self.cli("register", str(self.doc), "--id", "中文id", "--title", "中文 标题")
        self.assertIn("http://127.0.0.1:8798/d/" + quote("中文id"), output)
        self.assertIn(str(self.doc), output)
        registry = json.loads((self.state / "registry.local.json").read_text(encoding="utf-8"))
        self.assertEqual(set(registry), {"中文id"})
        self.assertEqual(registry["中文id"]["title"], "中文 标题")
        self.assertFalse((self.source / "registry.local.json").exists())
        self.assertIn("中文id", self.cli("list"))

    @unittest.skipUnless(os.name == "nt", "requires native Windows PowerShell 5.1")
    def test_powershell51_parser_and_owned_task_controls_with_mock_scheduler(self):
        powershell = str(Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" /
                         "v1.0" / "powershell.exe")
        # This process shadows every scheduler command used here; it never registers,
        # stops or inspects real tasks. Exercise actual installer ownership branches.
        script = r'''
$ErrorActionPreference = 'Stop'
$installer = Join-Path $env:RB_TEST_SOURCE 'install.ps1'
$tokens = $null
$errors = $null
[Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count) { throw ($errors | Out-String) }
function Import-Module { param($Name) }
function Get-ScheduledTask { param($TaskPath) return $global:mockTask }
function Disable-ScheduledTask { param($TaskName, $TaskPath) $global:mutations++; $global:mockTask.State = 'Disabled' }
function Stop-ScheduledTask { param($TaskName, $TaskPath) $global:mutations++; $global:mockTask.State = 'Disabled' }
function Unregister-ScheduledTask { param($TaskName, $TaskPath, $Confirm) $global:mutations++; $global:mockTask = $null }
$source = [IO.Path]::GetFullPath($env:RB_TEST_SOURCE)
$state = [IO.Path]::GetFullPath($env:RB_DATA_DIR)
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$utf8 = New-Object Text.UTF8Encoding($false)
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $tag = ([BitConverter]::ToString($sha.ComputeHash($utf8.GetBytes(($source + '|' + $state + '|' + $sid).ToLowerInvariant())))).Replace('-', '').Substring(0, 20)
} finally { $sha.Dispose() }
$name = 'AgentReviewBoard-' + $tag
$marker = 'agent-review-board.windows.v1:' + $tag + ':' + $sid
$config = [pscustomobject]@{ version=1; owner_sid=$sid; source_root=$source; data_dir=$state
    task_name=$name; marker=$marker; python=$env:RB_PYTHON; port=8798 }
$arguments = '-X utf8 -u "' + (Join-Path $source 'board_platform.py') + '" serve --data-dir "' + $state + '" --port 8798'
$global:mockTask = [pscustomobject]@{ TaskName=$name; TaskPath='\'; Description=$marker; State='Running'
    Principal=[pscustomobject]@{UserId=$sid; LogonType='Interactive'; RunLevel='Limited'}
    Actions=@([pscustomobject]@{Execute=$env:RB_PYTHON; Arguments=$arguments; WorkingDirectory=$source}) }
$global:mutations = 0
[IO.Directory]::CreateDirectory($state) | Out-Null
$manifest = Join-Path $state 'windows-service.json'
# A matching task without a manifest cannot be adopted.
$rejected = $false
try { & $installer -Action stop -DataDir $state } catch { $rejected = $true }
if (-not $rejected -or $global:mutations) { throw 'Missing-manifest ownership failure' }
[IO.File]::WriteAllText($manifest, ($config | ConvertTo-Json), $utf8)
# Inject registration failure only into a copy, with every scheduler operation mocked.
function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory) return @{} }
function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel) return @{} }
function New-ScheduledTaskTrigger { param([switch]$AtStartup,[switch]$AtLogOn,$User) return @{} }
function New-ScheduledTaskSettingsSet { param($MultipleInstances,[switch]$StartWhenAvailable,$RestartCount,$RestartInterval,$ExecutionTimeLimit,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries) return @{} }
function New-ScheduledTask { param($Action,$Trigger,$Principal,$Settings,$Description) return @{} }
function Register-ScheduledTask { param($TaskName,$TaskPath,$InputObject,[switch]$Force) throw 'injected registration failure' }
function Enable-ScheduledTask { param($TaskName,$TaskPath) $global:mockTask.State = 'Ready' }
function Start-ScheduledTask { param($TaskName,$TaskPath) $global:mockTask.State = 'Running' }
$failureInstaller = Join-Path $source 'install-failure.ps1'
$failureSource = [IO.File]::ReadAllText($installer, $utf8).Replace('Test-BeforeInstall $exe  # all validation precedes changes to registered services', '# preflight already covered independently')
[IO.File]::WriteAllText($failureInstaller, $failureSource, $utf8)
$rejected = $false
try { & $failureInstaller -Action install -DataDir $state -Python $env:RB_PYTHON } catch { $rejected = $true; $failureReason = ($_ | Out-String) }
if (-not $rejected -or $global:mockTask.State -ne 'Running') { throw ('Registration failure did not restore prior running state: ' + $global:mockTask.State + ' ' + $failureReason) }
$global:mutations = 0
# An altered action must be refused even if the name/description still match.
$global:mockTask.Actions[0].Arguments = 'foreign action'
$rejected = $false
try { & $installer -Action uninstall -DataDir $state } catch { $rejected = $true }
if (-not $rejected -or $global:mutations) { throw 'Foreign-action ownership failure' }
$global:mockTask.Actions[0].Arguments = $arguments
& $installer -Action stop -DataDir $state
& $installer -Action stop -DataDir $state
& $installer -Action uninstall -DataDir $state
$after = $global:mutations
& $installer -Action uninstall -DataDir $state
if ($global:mutations -ne $after -or $null -ne $global:mockTask -or (Test-Path -LiteralPath $manifest)) {
    throw 'Idempotent owned uninstall failed'
}
Write-Output 'MOCK-SCHEDULER-OK'
'''
        runner = self.root / "mock-scheduler.ps1"
        runner.write_text(script, encoding="utf-8")
        env = dict(self.env, RB_TEST_SOURCE=str(self.source))
        result = subprocess.run([powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                                 "-File", str(runner)], env=env,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("MOCK-SCHEDULER-OK", result.stdout)

    @unittest.skipUnless(os.name == "nt", "requires native Windows cmd.exe")
    def test_cmd_entrypoint_with_unicode_source_and_document_paths(self):
        self.assertGreaterEqual(sys.version_info, (3, 11))
        output = self.cli("register", str(self.doc), "--id", "cmd-fixture", use_cmd=True)
        self.assertIn("http://127.0.0.1:8798/d/cmd-fixture", output)
        self.assertIn(str(self.doc), output)

    def test_synthetic_handler_submit_to_cli_reply_loop(self):
        self.run_reply_loop()

    @unittest.skipUnless(os.name == "nt" or os.environ.get("RB_TEST_HTTP") == "1",
                         "native Windows or RB_TEST_HTTP=1 requires loopback sockets")
    def test_synthetic_http_submit_to_cli_reply_loop(self):
        self.run_reply_loop(http=True)

    def test_service_foreground_action_logs_utf8_and_propagates_failure(self):
        command = [sys.executable, "-X", "utf8", "-B", str(self.source / "board_platform.py"),
                   "serve", "--data-dir", str(self.state)]
        for source, expected in [("print('合成启动日志')\n", 0),
                                 ("raise RuntimeError('合成故障')\n", 1)]:
            (self.source / "server.py").write_text(source, encoding="utf-8")
            result = subprocess.run(command, env=self.env, cwd=self.root,
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, expected, result.stderr)
        log = (self.state / "logs" / "windows-server.log").read_text(encoding="utf-8")
        self.assertIn("合成启动日志", log)
        self.assertIn("RuntimeError: 合成故障", log)
        self.assertEqual(log.count("start pid="), 2)

    def run_reply_loop(self, http=False):
        self.cli("register", str(self.doc), "--id", "fixture", "--title", "合成标题")
        if http:
            with socket.socket() as reserved:
                reserved.bind(("127.0.0.1", 0))
                port = reserved.getsockname()[1]
            self.url = "http://127.0.0.1:%d" % port
            self.server = subprocess.Popen([sys.executable, "-X", "utf8", "-B",
                                            str(self.source / "board_platform.py"), "serve",
                                            "--data-dir", str(self.state), "--port", str(port)],
                                           env=self.env, cwd=self.root,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            deadline = time.monotonic() + 15
            while True:
                try:
                    self.request("/api/docs")
                    break
                except OSError:
                    if self.server.poll() is not None or time.monotonic() > deadline:
                        log = self.state / "logs" / "windows-server.log"
                        self.fail("synthetic server failed: " +
                                  (log.read_text(encoding="utf-8") if log.exists() else "no startup log"))
                    time.sleep(0.05)
        body = {"doc": "fixture", "kind": "doc", "text": "合成问题：为何如此？"}
        cid = self.request("/api/comment", body)["id"]
        self.assertEqual(json.loads(self.cli("pending", "--json")), [])
        result = self.request("/api/submit", {"doc": "fixture"})
        self.assertEqual(result["batch"], 1)
        pending = json.loads(self.cli("pending", "--doc", "fixture", "--json"))
        self.assertEqual(pending[0]["id"], cid)
        self.assertIn("合成问题", pending[0]["text"])
        answer = self.root / "中文 答复.md"
        answer.write_text("合成回答正文。\n", encoding="utf-8")
        self.cli("reply", "fixture", cid, "--file", str(answer), "--summary", "合成结论")
        self.assertEqual(json.loads(self.cli("pending", "--json")), [])
        comments_path, replies_path = lib.sidecar_paths(str(self.doc))
        comments = [json.loads(line) for line in Path(comments_path).read_text(encoding="utf-8").splitlines()]
        replies = [json.loads(line) for line in Path(replies_path).read_text(encoding="utf-8").splitlines()]
        self.assertIn("submit", [event["type"] for event in comments])
        self.assertEqual(replies[-1]["summary"], "合成结论")
        self.assertIn("reply", [event["type"] for event in replies])
        reply = next(event for event in replies if event["type"] == "reply")
        self.assertEqual(reply["author"], "codex")
        self.assertEqual(reply["session"], "synthetic-codex-thread")
        self.assertEqual(reply["harness"], "codex")
        self.assertEqual(self.doc.read_text(encoding="utf-8"), "# 合成标题\n\n需要解释的内容。\n")
        self.assertTrue((self.state / ".snapshots" / "fixture").is_dir())
        self.assertFalse(list(self.state.glob("*.jsonl")))
        self.assertFalse((self.source / "registry.local.json").exists())
        if http:
            self.assertIn("start pid=", (self.state / "logs" / "windows-server.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
