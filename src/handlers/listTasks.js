'use strict';
const { wrap } = require('../lib/http');
const { validateListQuery } = require('../lib/validation');
const { getRepository } = require('../lib/repository');

exports.handler = wrap(async (event) => {
  const query = validateListQuery(event.queryStringParameters || {});
  const { items, nextToken } = await getRepository().list(query);
  return { statusCode: 200, body: { data: items, count: items.length, nextToken } };
});
