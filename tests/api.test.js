'use strict';
const { test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const os = require('os');
const path = require('path');
const fs = require('fs');

process.env.STORAGE = 'file';
process.env.CORS_ORIGIN = '*';

const { resetRepositoryForTests } = require('../src/lib/repository');
const createTask = require('../src/handlers/createTask').handler;
const getTask = require('../src/handlers/getTask').handler;
const listTasks = require('../src/handlers/listTasks').handler;
const updateTask = require('../src/handlers/updateTask').handler;
const deleteTask = require('../src/handlers/deleteTask').handler;

const UNKNOWN_ID = '11111111-1111-4111-8111-111111111111';

const ev = (method, { body, id, query } = {}) => ({
  requestContext: { http: { method } },
  pathParameters: id ? { id } : undefined,
  queryStringParameters: query,
  body: body === undefined ? undefined : typeof body === 'string' ? body : JSON.stringify(body),
});
const json = (res) => (res.body ? JSON.parse(res.body) : null);

beforeEach(() => {
  process.env.LOCAL_DB_FILE = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'tasks-')), 'db.json');
  resetRepositoryForTests();
});

test('POST creates a task -> 201 with CORS headers', async () => {
  const res = await createTask(ev('POST', { body: { title: 'Write docs', priority: 'high' } }));
  assert.equal(res.statusCode, 201);
  assert.equal(res.headers['Access-Control-Allow-Origin'], '*');
  const { data } = json(res);
  assert.equal(data.status, 'todo');
  assert.equal(data.priority, 'high');
  assert.match(data.id, /^[0-9a-f-]{36}$/);
});

test('POST rejects malformed payloads -> 400', async () => {
  const bad = [
    { body: undefined },
    { body: '{not json' },
    { body: '[]' },
    { body: {} },
    { body: { title: '' } },
    { body: { title: 123 } },
    { body: { title: 'x'.repeat(101) } },
    { body: { title: 'ok', status: 'nope' } },
    { body: { title: 'ok', dueDate: '2025-13-40' } },
    { body: { title: 'ok', hacker: true } },
  ];
  for (const b of bad) {
    const res = await createTask(ev('POST', b));
    assert.equal(res.statusCode, 400, JSON.stringify(b));
    assert.ok(json(res).error.code);
  }
});

test('GET by id -> 200, unknown -> 404, malformed id -> 400', async () => {
  const created = json(await createTask(ev('POST', { body: { title: 'A' } }))).data;
  assert.equal((await getTask(ev('GET', { id: created.id }))).statusCode, 200);
  assert.equal((await getTask(ev('GET', { id: UNKNOWN_ID }))).statusCode, 404);
  assert.equal((await getTask(ev('GET', { id: 'abc' }))).statusCode, 400);
});

test('PATCH updates fields, validates, and 404s for unknown', async () => {
  const created = json(await createTask(ev('POST', { body: { title: 'A' } }))).data;
  const ok = await updateTask(ev('PATCH', { id: created.id, body: { status: 'done', dueDate: '2030-01-31' } }));
  assert.equal(ok.statusCode, 200);
  assert.equal(json(ok).data.status, 'done');
  assert.equal(json(ok).data.title, 'A');

  assert.equal((await updateTask(ev('PATCH', { id: created.id, body: {} }))).statusCode, 400);
  assert.equal((await updateTask(ev('PATCH', { id: created.id, body: { status: 'x' } }))).statusCode, 400);
  assert.equal((await updateTask(ev('PATCH', { id: UNKNOWN_ID, body: { title: 'x' } }))).statusCode, 404);
});

test('GET list supports status filter + pagination', async () => {
  for (let i = 0; i < 3; i++) {
    await createTask(ev('POST', { body: { title: `T${i}`, status: i === 0 ? 'done' : 'todo' } }));
  }
  const page1 = json(await listTasks(ev('GET', { query: { limit: '2' } })));
  assert.equal(page1.count, 2);
  assert.ok(page1.nextToken);
  const page2 = json(await listTasks(ev('GET', { query: { limit: '2', nextToken: page1.nextToken } })));
  assert.equal(page2.count, 1);
  const done = json(await listTasks(ev('GET', { query: { status: 'done' } })));
  assert.equal(done.count, 1);
  assert.equal((await listTasks(ev('GET', { query: { limit: '0' } }))).statusCode, 400);
});

test('DELETE -> 204 then 404', async () => {
  const created = json(await createTask(ev('POST', { body: { title: 'A' } }))).data;
  assert.equal((await deleteTask(ev('DELETE', { id: created.id }))).statusCode, 204);
  assert.equal((await deleteTask(ev('DELETE', { id: created.id }))).statusCode, 404);
});

test('OPTIONS preflight -> 204 with CORS headers', async () => {
  const res = await createTask(ev('OPTIONS'));
  assert.equal(res.statusCode, 204);
  assert.match(res.headers['Access-Control-Allow-Methods'], /POST/);
  assert.match(res.headers['Access-Control-Allow-Headers'], /Content-Type/);
});
