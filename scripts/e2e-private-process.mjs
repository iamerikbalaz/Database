// This broker receives its entire request through a private pipe. Its only
// output is a fixed started event and result; target output goes straight to NUL.
import { spawn } from 'node:child_process';
import { isAbsolute } from 'node:path';
import { statSync } from 'node:fs';

const MAX_REQUEST_BYTES = 32 * 1024 * 1024;
const KILL_GRACE_MS = 5_000;
let target;
let targetStarted = false;
let targetClosed = false;
let finished = false;
let timer;
let stopping;

function result(started, completed, exitCode, errorCategory) {
  return {
    started,
    completed,
    exit_code: Number.isInteger(exitCode) ? exitCode : null,
    error_category: errorCategory,
  };
}

function finish(value) {
  if (finished) return;
  finished = true;
  clearTimeout(timer);
  process.stdout.write(`${JSON.stringify(value)}\n`, () => process.exit(0));
}

async function killOwnedTarget() {
  if (!target || targetClosed) return;
  if (!targetStarted) {
    // Cancellation can arrive between spawn() and its asynchronous spawn event.
    await new Promise((resolve) => {
      const complete = () => {
        clearTimeout(limit);
        target.removeListener('spawn', complete);
        target.removeListener('error', complete);
        resolve();
      };
      const limit = setTimeout(complete, KILL_GRACE_MS);
      target.once('spawn', complete);
      target.once('error', complete);
    });
  }
  if (!targetStarted || targetClosed) return;
  const waitForClose = () => new Promise((resolve) => {
    if (targetClosed) { resolve(); return; }
    const complete = () => {
      clearTimeout(limit);
      target.removeListener('close', complete);
      resolve();
    };
    const limit = setTimeout(complete, KILL_GRACE_MS);
    target.once('close', complete);
  });
  if (target.exitCode !== null || target.signalCode !== null) {
    await waitForClose();
    return;
  }
  if (process.platform !== 'win32') {
    // The target owns a separate process group on POSIX.
    try { process.kill(-target.pid, 'SIGKILL'); } catch { /* Already exited. */ }
    await waitForClose();
    return;
  }
  await new Promise((resolve) => {
    let killer;
    let done = false;
    const complete = () => {
      if (done) return;
      done = true;
      clearTimeout(limit);
      resolve();
    };
    const limit = setTimeout(() => {
      try { killer?.kill(); } catch { /* No diagnostic from cleanup. */ }
      try { target.kill(); } catch { /* No unrelated process is selected. */ }
      complete();
    }, KILL_GRACE_MS);
    try {
      killer = spawn('taskkill.exe', ['/PID', String(target.pid), '/T', '/F'], {
        shell: false, windowsHide: true, stdio: 'ignore',
      });
      killer.once('error', () => {
        try { target.kill(); } catch { /* Already exited. */ }
        complete();
      });
      killer.once('close', (code) => {
        if (code !== 0) {
          try { target.kill(); } catch { /* Already exited. */ }
        }
        complete();
      });
    } catch {
      try { target.kill(); } catch { /* Already exited. */ }
      complete();
    }
  });
  // taskkill can be denied in a sandbox. The fallback above uses the actual
  // ChildProcess handle; wait for its close event before releasing the caller.
  await waitForClose();
}

function stop(errorCategory) {
  if (finished || stopping) return;
  stopping = errorCategory;
  clearTimeout(timer);
  void killOwnedTarget().finally(() => {
    finish(result(targetStarted, false, null, errorCategory));
  });
}

function run(request) {
  if (!request || typeof request !== 'object' || Array.isArray(request) ||
      typeof request.file_path !== 'string' || request.file_path.length === 0 ||
      !Array.isArray(request.arguments) || !request.arguments.every((value) => typeof value === 'string') ||
      typeof request.standard_input !== 'string' || typeof request.working_directory !== 'string' ||
      !isAbsolute(request.working_directory) ||
      !Number.isInteger(request.timeout_ms) || request.timeout_ms < 1 || request.timeout_ms > 3_600_000) {
    finish(result(false, false, null, 'invalid_state'));
    return;
  }
  try {
    if (!statSync(request.working_directory).isDirectory()) throw new Error();
  } catch {
    finish(result(false, false, null, 'invalid_state'));
    return;
  }
  // Buffer.from adds neither a BOM nor a newline. Only the caller decides the
  // SQL payload or the exact two newline-terminated bootstrap input lines.
  const input = Buffer.from(request.standard_input, 'utf8');
  request.standard_input = '';
  let inputComplete = false;
  try {
    target = spawn(request.file_path, request.arguments, {
      cwd: request.working_directory,
      shell: false,
      windowsHide: true,
      detached: process.platform !== 'win32',
      // The OS drains both output channels without buffering or exposing them.
      stdio: ['pipe', 'ignore', 'ignore'],
    });
  } catch {
    input.fill(0);
    finish(result(false, false, null, 'process_start'));
    return;
  }
  target.once('error', () => {
    input.fill(0);
    if (!targetStarted) finish(result(false, false, null, 'process_start'));
    else stop('unknown_safe_failure');
  });
  target.stdin.once('error', () => stop('stdin_io'));
  target.once('spawn', () => {
    targetStarted = true;
    if (stopping) return;
    // A fixed protocol event lets the caller distinguish launch from execution.
    // It never contains target arguments, output, credentials or request data.
    process.stdout.write('{"event":"started"}\n');
    timer = setTimeout(() => stop('timeout'), request.timeout_ms);
    target.stdin.end(input, (error) => {
      input.fill(0);
      if (error) stop('stdin_io');
      else inputComplete = true;
    });
  });
  target.once('close', (code, signal) => {
    targetClosed = true;
    input.fill(0);
    if (stopping || finished) return;
    if (!targetStarted) finish(result(false, false, null, 'process_start'));
    else if (!inputComplete) finish(result(true, false, null, 'stdin_io'));
    else if (signal || !Number.isInteger(code)) finish(result(true, false, null, 'unknown_safe_failure'));
    else finish(result(true, true, code, code === 0 ? null : 'nonzero_exit'));
  });
}

const chunks = [];
let inputBytes = 0;
let requestRead = false;
let control = Buffer.alloc(0);

function readRequest() {
  requestRead = true;
  let request;
  const bytes = Buffer.concat(chunks);
  try {
    // Windows PowerShell's envelope pipe can have a platform writer preamble;
    // this never changes the actual standard_input string inside the envelope.
    request = JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/, ''));
  } catch {
    finish(result(false, false, null, 'invalid_state'));
    return;
  } finally {
    bytes.fill(0);
    chunks.forEach((value) => value.fill(0));
    chunks.length = 0;
  }
  run(request);
}

function readControl(chunk) {
  if (finished || chunk.length === 0) return;
  if (control.length + chunk.length > 128) { stop('unknown_safe_failure'); return; }
  control = Buffer.concat([control, chunk]);
  const end = control.indexOf(10);
  if (end !== -1) {
    // A closed/cancelled caller can only stop its own already-started target.
    // No arbitrary command, PID or data is accepted through this control frame.
    if (control.subarray(0, end).toString('utf8') === '{"event":"cancel"}') {
      stop('unknown_safe_failure');
    } else {
      stop('invalid_state');
    }
    control.fill(0);
    control = Buffer.alloc(0);
  }
}

process.stdin.on('data', (chunk) => {
  if (finished) return;
  if (requestRead) { readControl(chunk); return; }
  const end = chunk.indexOf(10);
  const part = end === -1 ? chunk : chunk.subarray(0, end);
  inputBytes += part.length;
  if (inputBytes > MAX_REQUEST_BYTES) {
    process.stdin.pause();
    chunks.forEach((value) => value.fill(0));
    finish(result(false, false, null, 'invalid_state'));
    return;
  }
  chunks.push(part);
  if (end !== -1) {
    readRequest();
    readControl(chunk.subarray(end + 1));
  }
});
process.stdin.once('error', () => {
  if (requestRead) stop('stdin_io');
  else finish(result(false, false, null, 'stdin_io'));
});
process.stdin.once('end', () => {
  if (finished) return;
  if (!requestRead) {
    chunks.forEach((value) => value.fill(0));
    finish(result(false, false, null, 'stdin_io'));
  }
  else stop('unknown_safe_failure');
});
process.once('SIGTERM', () => stop('unknown_safe_failure'));
process.once('SIGINT', () => stop('unknown_safe_failure'));
process.once('uncaughtException', () => stop('unknown_safe_failure'));
process.once('unhandledRejection', () => stop('unknown_safe_failure'));
