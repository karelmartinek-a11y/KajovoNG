// Nezávislé ověření referenčních bytes; ne produkční audit ani strict parser.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

export function auditHash(version, previous, sequence, body) {
  assert.equal(version, 1);
  assert.equal(previous.length, 32);
  assert(sequence >= 1n && sequence <= 9223372036854775807n);
  const header = Buffer.alloc(4 + 32 + 8 + 8);
  header.writeUInt32BE(version, 0);
  previous.copy(header, 4);
  header.writeBigUInt64BE(sequence, 36);
  header.writeBigUInt64BE(BigInt(body.length), 44);
  return createHash('sha256').update(Buffer.from('KCML-AUDIT\0', 'ascii'))
    .update(header).update(body).digest('hex');
}

function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value !== null && typeof value === 'object') {
    return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
  }
  return JSON.stringify(value);
}

const root = new URL('./', import.meta.url);
for (const item of JSON.parse(readFileSync(new URL('canonical-vectors.json', root), 'utf8')).accepted) {
  assert.equal(canonical(JSON.parse(item.input)), item.canonical, item.name);
}
const vectors = JSON.parse(readFileSync(new URL('audit-vectors.json', root), 'utf8'));
for (const vector of vectors) {
  assert.equal(auditHash(1, Buffer.from(vector.previous_hash, 'hex'), BigInt(vector.sequence),
    Buffer.from(vector.canonical_hex, 'hex')), vector.expected_sha256, vector.name);
}
assert.throws(() => auditHash(1, Buffer.alloc(32), 0n, Buffer.from('{}')));
assert.throws(() => auditHash(2, Buffer.alloc(32), 1n, Buffer.from('{}')));
assert.throws(() => auditHash(1, Buffer.alloc(31), 1n, Buffer.from('{}')));
console.log(`Canonical accepted vectors: 7; audit vectors: ${vectors.length}; boundary rejections: 3 — PASS`);
