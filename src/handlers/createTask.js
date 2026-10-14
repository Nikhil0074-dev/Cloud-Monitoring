'use strict';
const { randomUUID } = require('crypto');
const { wrap, parseJsonBody } = require('../lib/http');
const { validateTaskInput } = require('../lib/validation');
const { getRepository } = require('../lib/repository');

exports.handler = wrap(async (event) => {
  const input = validateTaskInput(parseJsonBody(event), { partial: false });
  const now = new Date().toISOString();
  const task = {
    id: randomUUID(),
    title: input.title,
    description: input.description ?? '',
    status: input.status ?? 'todo',
    priority: input.priority ?? 'medium',
    dueDate: input.dueDate ?? null,
    createdAt: now,
    updatedAt: now,
  };
  await getRepository().create(task);
  return { statusCode: 201, body: { data: task }, headers: { Location: `/tasks/${task.id}` } };
});
