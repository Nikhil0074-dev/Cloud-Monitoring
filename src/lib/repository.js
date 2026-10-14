'use strict';

/**
 * Storage layer. Picks a backend once per container (cold start):
 *   - DynamoDB  : deployed on AWS (default)
 *   - JSON file : local demo (serverless-offline sets IS_OFFLINE) or STORAGE=file (tests)
 */
const { NotFoundError, ValidationError } = require('./errors');

let repo; // cached across warm invocations

function getRepository() {
  if (!repo) {
    const useFile = process.env.STORAGE === 'file' || (process.env.IS_OFFLINE && process.env.STORAGE !== 'dynamodb');
    repo = useFile ? createFileRepository() : createDynamoRepository();
  }
  return repo;
}

/* ------------------------------ DynamoDB ------------------------------ */
function createDynamoRepository() {
  // Lazy require + client created once per cold start, reused on warm invocations.
  // The AWS SDK v3 is provided by the Lambda Node 18/20 runtime (devDependency only).
  const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
  const { DynamoDBDocumentClient, PutCommand, GetCommand, UpdateCommand, DeleteCommand, ScanCommand } =
    require('@aws-sdk/lib-dynamodb');

  const client = DynamoDBDocumentClient.from(new DynamoDBClient({}), {
    marshallOptions: { removeUndefinedValues: true },
  });
  const TableName = process.env.TASKS_TABLE;

  const isConditionFailed = (e) => e && e.name === 'ConditionalCheckFailedException';

  return {
    async create(task) {
      await client.send(new PutCommand({
        TableName, Item: task, ConditionExpression: 'attribute_not_exists(id)',
      }));
      return task;
    },

    async get(id) {
      const { Item } = await client.send(new GetCommand({ TableName, Key: { id } }));
      return Item || null;
    },

    async list({ limit, status, nextToken }) {
      let startKey;
      if (nextToken) {
        try {
          const decoded = JSON.parse(Buffer.from(nextToken, 'base64url').toString('utf8'));
          if (!decoded || typeof decoded.id !== 'string') throw new Error('bad token');
          startKey = { id: decoded.id };
        } catch {
          throw new ValidationError([{ field: 'nextToken', message: 'nextToken is invalid' }]);
        }
      }

      const items = [];
      let lastKey = startKey;
      // Scan's Limit applies BEFORE filtering, so loop until we have enough or run out.
      do {
        const params = { TableName, ExclusiveStartKey: lastKey, Limit: status ? 100 : limit - items.length };
        if (status) {
          params.FilterExpression = '#s = :s';
          params.ExpressionAttributeNames = { '#s': 'status' };
          params.ExpressionAttributeValues = { ':s': status };
        }
        const res = await client.send(new ScanCommand(params));
        items.push(...(res.Items || []));
        lastKey = res.LastEvaluatedKey;
      } while (lastKey && items.length < limit);

      let next = null;
      let page = items;
      if (items.length > limit) {
        page = items.slice(0, limit);
        next = Buffer.from(JSON.stringify({ id: page[page.length - 1].id })).toString('base64url');
      } else if (lastKey) {
        next = Buffer.from(JSON.stringify({ id: lastKey.id })).toString('base64url');
      }
      return { items: page, nextToken: next };
    },

    async update(id, changes) {
      const names = {};
      const values = {};
      const sets = [];
      Object.entries(changes).forEach(([k, v], i) => {
        names[`#f${i}`] = k;
        values[`:v${i}`] = v;
        sets.push(`#f${i} = :v${i}`);
      });
      try {
        const { Attributes } = await client.send(new UpdateCommand({
          TableName,
          Key: { id },
          UpdateExpression: `SET ${sets.join(', ')}`,
          ConditionExpression: 'attribute_exists(id)',
          ExpressionAttributeNames: names,
          ExpressionAttributeValues: values,
          ReturnValues: 'ALL_NEW',
        }));
        return Attributes;
      } catch (e) {
        if (isConditionFailed(e)) throw new NotFoundError();
        throw e;
      }
    },

    async remove(id) {
      try {
        await client.send(new DeleteCommand({
          TableName, Key: { id }, ConditionExpression: 'attribute_exists(id)',
        }));
      } catch (e) {
        if (isConditionFailed(e)) throw new NotFoundError();
        throw e;
      }
    },
  };
}

/* ------------------------------ JSON file ------------------------------ */
function createFileRepository() {
  const fs = require('fs');
  const path = require('path');
  const file = process.env.LOCAL_DB_FILE || path.join(process.cwd(), '.local-db', 'tasks.json');

  const read = () => {
    try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return {}; }
  };
  const write = (data) => {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const tmp = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(data, null, 2));
    fs.renameSync(tmp, file);
  };

  return {
    async create(task) { const db = read(); db[task.id] = task; write(db); return task; },
    async get(id) { return read()[id] || null; },
    async list({ limit, status, nextToken }) {
      let offset = 0;
      if (nextToken) {
        offset = Number(Buffer.from(nextToken, 'base64url').toString('utf8'));
        if (!Number.isInteger(offset) || offset < 0) {
          throw new ValidationError([{ field: 'nextToken', message: 'nextToken is invalid' }]);
        }
      }
      let all = Object.values(read()).sort((a, b) => a.createdAt.localeCompare(b.createdAt));
      if (status) all = all.filter((t) => t.status === status);
      const items = all.slice(offset, offset + limit);
      const end = offset + limit;
      return { items, nextToken: end < all.length ? Buffer.from(String(end)).toString('base64url') : null };
    },
    async update(id, changes) {
      const db = read();
      if (!db[id]) throw new NotFoundError();
      db[id] = { ...db[id], ...changes };
      write(db);
      return db[id];
    },
    async remove(id) {
      const db = read();
      if (!db[id]) throw new NotFoundError();
      delete db[id];
      write(db);
    },
  };
}

function resetRepositoryForTests() { repo = undefined; }

module.exports = { getRepository, resetRepositoryForTests };
