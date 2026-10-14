'use strict';
const { wrap } = require('../lib/http');
const { validateId } = require('../lib/validation');
const { getRepository } = require('../lib/repository');
const { NotFoundError } = require('../lib/errors');

exports.handler = wrap(async (event) => {
  const id = validateId(event.pathParameters?.id);
  const task = await getRepository().get(id);
  if (!task) throw new NotFoundError();
  return { statusCode: 200, body: { data: task } };
});
