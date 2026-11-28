"use strict";
// In-memory version of the browser's storage, for the tests.
class MemoryStore {
  constructor() { this.m = new Map(); }
  get(n) { return this.m.has(n) ? this.m.get(n) : null; }
  put(n, b) { this.m.set(n, Uint8Array.from(b)); }
  remove(n) { this.m.delete(n); }
  list() { return [...this.m.keys()]; }
}
module.exports = { MemoryStore };
