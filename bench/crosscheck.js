// Cross-implementation proof.
//
//   legacy_reference.py (PyYAML, YAML 1.1)  ->  how the original file read
//   this script        (yaml npm, YAML 1.2) ->  how the migrated file reads
//
// If they agree, the migration preserved meaning ACROSS implementations, which
// is the only claim worth making. A same-library round-trip would prove
// nothing: PyYAML reads its own output happily even when that output is
// ambiguous to everyone else.
//
// Note on the reference: PyYAML is chosen as the 1.1 oracle because it is what
// the affected projects actually use. It is NOT the same as the `yaml` npm
// package's 1.1 mode -- they disagree on `1e3` (string vs number), which is
// itself evidence that "YAML 1.1" was never a single fixed semantics.

const fs = require('fs');
const path = require('path');
const YAML = require('yaml');

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'bench', 'out');
const legacy = JSON.parse(fs.readFileSync(path.join(__dirname, 'legacy.json'), 'utf8'));

function flatten(obj, prefix = '', acc = {}) {
  if (obj === null || typeof obj !== 'object') {
    acc[prefix] = obj;
    return acc;
  }
  if (Array.isArray(obj)) {
    obj.forEach((v, i) => flatten(v, `${prefix}[${i}]`, acc));
    return acc;
  }
  for (const k of Object.keys(obj)) {
    flatten(obj[k], prefix ? `${prefix}.${k}` : k, acc);
  }
  return acc;
}

function same(a, b) {
  if (a === b) return true;
  if (typeof a === 'number' && typeof b === 'number') {
    if (Number.isNaN(a) && Number.isNaN(b)) return true;
    return Math.abs(a - b) <= Math.max(Math.abs(a), 1) * 1e-12;
  }
  return false;
}

// Key normalisation.
//
// PyYAML reads a bare `on:` key as boolean true, so the reference JSON has the
// string "True" where the 1.2 side has "on". After migration the key is quoted
// and BOTH readers see "on" -- the migration fixed it. Comparing raw key text
// would flag that fix as a failure, so boolean-looking keys are normalised to
// their lowercase source spelling on both sides.
function normKey(k) {
  return k
    .split('.')
    .map((part) => {
      const lower = part.toLowerCase();
      if (lower === 'true') return 'on';
      if (lower === 'false') return 'off';
      return part;
    })
    .join('.');
}

function normalise(obj) {
  const out = {};
  for (const [k, v] of Object.entries(obj)) out[normKey(k)] = v;
  return out;
}

const rows = [];
let failures = 0;

for (const file of Object.keys(legacy)) {
  const valuePath = path.join(OUT, 'value', file);
  const textPath = path.join(OUT, 'text', file);
  if (!fs.existsSync(valuePath) && !fs.existsSync(textPath)) {
    rows.push({ file, status: 'unchanged (no ambiguous scalars)' });
    continue;
  }

  const reference = normalise(legacy[file]);
  const entry = { file, value: null, text: null };

  if (fs.existsSync(valuePath)) {
    const modern = normalise(flatten(YAML.parse(fs.readFileSync(valuePath, 'utf8'))));
    const diffs = [];
    for (const k of new Set([...Object.keys(reference), ...Object.keys(modern)])) {
      if (!same(reference[k], modern[k])) {
        diffs.push({ key: k, pyyaml_1_1: reference[k], yaml_1_2: modern[k] });
      }
    }
    entry.value = { mismatches: diffs.length, sample: diffs.slice(0, 6) };
    if (diffs.length) failures += 1;
  }

  if (fs.existsSync(textPath)) {
    const modern = normalise(flatten(YAML.parse(fs.readFileSync(textPath, 'utf8'))));
    const notPinned = [];
    for (const k of Object.keys(reference)) {
      const before = reference[k];
      const after = modern[k];
      if (same(before, after)) continue;
      if (typeof after === 'string') continue; // documented text-pin
      notPinned.push({ key: k, pyyaml_1_1: before, yaml_1_2: after });
    }
    entry.text = { notTextPinned: notPinned.length, sample: notPinned.slice(0, 6) };
    if (notPinned.length) failures += 1;
  }

  rows.push(entry);
}

console.log(JSON.stringify({ failingFiles: failures, rows }, null, 2));
process.exit(failures ? 1 : 0);
