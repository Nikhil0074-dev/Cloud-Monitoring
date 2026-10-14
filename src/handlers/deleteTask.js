'use strict';
const { wrap } = require('../lib/http');
const { validateId } = require('../lib/validation');
const { getRepository } = require('../lib/repository');

exports.handler = wrap(async (event) => {
  const id = validateId(event.pathParameters?.id);
  await getRepository().remove(id);
  return { statusCode: 204 };
});
