#!/usr/bin/env node
const { spawnSync } = require('node:child_process');

const args = process.argv.slice(2);
const cmd = process.env.PYTHON || 'python3';
const result = spawnSync(cmd, ['-m', 'tagme.cli', ...args], {
  stdio: 'inherit'
});

if (result.error) {
  console.error('[tagme] Failed to run python module:', result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 0);
