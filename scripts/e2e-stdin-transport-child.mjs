// Test child: secrets arrive only on stdin and never enter command arguments/files.
import { createHash } from 'node:crypto';
import { existsSync, writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';

const [mode, ...args] = process.argv.slice(2);
const fail = () => { process.exitCode = 91; };

if (mode === 'closed-stdin') {
  // Exiting without reading closes every inherited Windows stdin handle. The
  // parent writes several MiB, which cannot fit in the unconsumed pipe buffer.
  process.exit(0);
} else if (mode === 'timeout-tree') {
  spawn(process.execPath, [import.meta.filename, 'delayed-marker', args[0]], {
    shell: false,
    stdio: 'ignore',
  });
  setTimeout(() => process.exit(0), 15000);
} else if (mode === 'delayed-marker') {
  setTimeout(() => { writeFileSync(args[0], 'survived'); }, 1800);
} else if (mode === 'callback-gate') {
  // This child cannot complete until the parent's started callback releases it.
  // The marker contains only a fixed public word, never stdin or credentials.
  const deadline = Date.now() + 5000;
  const gate = setInterval(() => {
    if (existsSync(args[0])) {
      clearInterval(gate);
      process.exit(Number(args[1]));
    } else if (Date.now() >= deadline) {
      clearInterval(gate);
      process.exit(91);
    }
  }, 10);
  process.stdin.resume();
} else {
  const chunks = [];
  process.stdin.on('data', chunk => chunks.push(chunk));
  process.stdin.on('error', fail);
  process.stdin.on('end', () => {
    try {
      const received = Buffer.concat(chunks);
      if (mode === 'arguments') {
        const expected = [
          '', 'two words', 'double"quote', "single'quote", 'backslash\\',
          'quote\\"end', '\u017elu\u0165ou\u010dk\u00fd', '$(exit 99)', '& exit 99',
        ];
        if (JSON.stringify(args) !== JSON.stringify(expected) || received.length !== 0) fail();
      } else if (mode === 'bytes' || mode === 'nonzero') {
        const [expectedHash, expectedLength, expectedLines] = args;
        const actualHash = createHash('sha256').update(received).digest('hex');
        const lineCount = received.reduce((count, byte) => count + (byte === 10 ? 1 : 0), 0);
        if (
          actualHash !== expectedHash || received.length !== Number(expectedLength) ||
          lineCount !== Number(expectedLines) ||
          received.subarray(0, 3).equals(Buffer.from([0xef, 0xbb, 0xbf]))
        ) { fail(); return; }
        // Deliberately hostile output: the parent must suppress both streams,
        // including enough output to exceed ordinary OS pipe buffers.
        for (let index = 0; index < 128; index += 1) {
          process.stdout.write(received);
          process.stderr.write(received);
        }
        if (mode === 'nonzero') process.exitCode = 23;
      } else {
        fail();
      }
    } catch {
      fail();
    }
  });
}
