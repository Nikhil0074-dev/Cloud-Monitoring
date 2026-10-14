'use strict';

const { HttpError } = require('./errors');

const MAX_BODY_BYTES = 100 * 1024; // 100 KB

function corsHeaders() {
  return {
    'Access-Control-Allow-Origin': process.env.CORS_ORIGIN || '*',
    'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type,Authorization,X-Requested-With',
    'Access-Control-Max-Age': '86400',
  };
}

function response(statusCode, body, extraHeaders = {}) {
  return {
    statusCode,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
      ...corsHeaders(),
      ...extraHeaders,
    },
    body: body === undefined ? '' : JSON.stringify(body),
  };
}

function parseJsonBody(event) {
  if (event.body === undefined || event.body === null || event.body === '') {
    throw new HttpError(400, 'BAD_REQUEST', 'Request body is required');
  }
  let raw = event.body;
  if (event.isBase64Encoded) raw = Buffer.from(raw, 'base64').toString('utf8');
  if (Buffer.byteLength(raw, 'utf8') > MAX_BODY_BYTES) {
    throw new HttpError(413, 'PAYLOAD_TOO_LARGE', 'Request body exceeds 100 KB');
  }
  try {
    return JSON.parse(raw);
  } catch {
    throw new HttpError(400, 'INVALID_JSON', 'Request body is not valid JSON');
  }
}

/**
 * Wraps a handler: CORS preflight, uniform JSON responses, central error handling.
 * Inner handlers return { statusCode, body, headers? } or throw HttpError.
 */
function wrap(fn) {
  return async (event) => {
    const method = event?.requestContext?.http?.method || event?.httpMethod;
    if (method === 'OPTIONS') return response(204);

    try {
      const result = await fn(event);
      return response(result.statusCode, result.body, result.headers);
    } catch (err) {
      if (err instanceof HttpError) {
        return response(err.statusCode, {
          error: { code: err.code, message: err.message, ...(err.details && { details: err.details }) },
        });
      }
      console.error('Unhandled error:', err);
      return response(500, {
        error: { code: 'INTERNAL_ERROR', message: 'Internal server error' },
      });
    }
  };
}

module.exports = { wrap, response, parseJsonBody, corsHeaders };
