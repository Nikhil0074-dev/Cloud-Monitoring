'use strict';
const { wrap, parseJsonBody } = require('../lib/http');
const { validateTaskInput, validateId } = require('../lib/validation');
const { getRepository } = require('../lib/repository');

exports.handler = wrap(async (event) => {
  const id = validateId(event.pathParameters?.id);
  const changes = validateTaskInput(parseJsonBody(event), { partial: true });
  changes.updatedAt = new Date().toISOString();
  const task = await getRepository().update(id, changes);
  return { statusCode: 200, body: { data: task } };
});
