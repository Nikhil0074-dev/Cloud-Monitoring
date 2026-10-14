'use strict';

const { ValidationError } = require('./errors');

const STATUSES = ['todo', 'in_progress', 'done'];
const PRIORITIES = ['low', 'medium', 'high'];
const ALLOWED_FIELDS = ['title', 'description', 'status', 'priority', 'dueDate'];
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

const isPlainObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

function isValidDate(str) {
  if (!DATE_RE.test(str)) return false;
  const d = new Date(`${str}T00:00:00.000Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === str;
}

/**
 * Validates a task payload.
 * @param {*} body parsed JSON body
 * @param {{partial: boolean}} opts partial=true for updates (all fields optional, >=1 required)
 * @returns validated + normalized object (throws ValidationError otherwise)
 */
function validateTaskInput(body, { partial = false } = {}) {
  if (!isPlainObject(body)) {
    throw new ValidationError([{ field: 'body', message: 'Body must be a JSON object' }]);
  }

  const errors = [];
  const out = {};

  for (const key of Object.keys(body)) {
    if (!ALLOWED_FIELDS.includes(key)) {
      errors.push({ field: key, message: `Unknown field "${key}"` });
    }
  }

  // title
  if (body.title === undefined) {
    if (!partial) errors.push({ field: 'title', message: 'title is required' });
  } else if (typeof body.title !== 'string') {
    errors.push({ field: 'title', message: 'title must be a string' });
  } else {
    const t = body.title.trim();
    if (t.length < 1 || t.length > 100) {
      errors.push({ field: 'title', message: 'title must be 1-100 characters' });
    } else out.title = t;
  }

  // description
  if (body.description !== undefined) {
    if (typeof body.description !== 'string') {
      errors.push({ field: 'description', message: 'description must be a string' });
    } else if (body.description.length > 500) {
      errors.push({ field: 'description', message: 'description must be at most 500 characters' });
    } else out.description = body.description;
  }

  // status
  if (body.status !== undefined) {
    if (!STATUSES.includes(body.status)) {
      errors.push({ field: 'status', message: `status must be one of: ${STATUSES.join(', ')}` });
    } else out.status = body.status;
  }

  // priority
  if (body.priority !== undefined) {
    if (!PRIORITIES.includes(body.priority)) {
      errors.push({ field: 'priority', message: `priority must be one of: ${PRIORITIES.join(', ')}` });
    } else out.priority = body.priority;
  }

  // dueDate (null clears it on update)
  if (body.dueDate !== undefined) {
    if (body.dueDate === null && partial) {
      out.dueDate = null;
    } else if (typeof body.dueDate !== 'string' || !isValidDate(body.dueDate)) {
      errors.push({ field: 'dueDate', message: 'dueDate must be a valid date in YYYY-MM-DD format' });
    } else out.dueDate = body.dueDate;
  }

  if (partial && Object.keys(body).length === 0) {
    errors.push({ field: 'body', message: 'Provide at least one field to update' });
  }

  if (errors.length) throw new ValidationError(errors);
  return out;
}

function validateId(id) {
  if (typeof id !== 'string' || !UUID_RE.test(id)) {
    throw new ValidationError([{ field: 'id', message: 'id must be a valid UUID' }], 'Invalid task id');
  }
  return id.toLowerCase();
}

function validateListQuery(query = {}) {
  const errors = [];
  const out = { limit: 20 };

  if (query.limit !== undefined) {
    const n = Number(query.limit);
    if (!Number.isInteger(n) || n < 1 || n > 100) {
      errors.push({ field: 'limit', message: 'limit must be an integer between 1 and 100' });
    } else out.limit = n;
  }
  if (query.status !== undefined) {
    if (!STATUSES.includes(query.status)) {
      errors.push({ field: 'status', message: `status must be one of: ${STATUSES.join(', ')}` });
    } else out.status = query.status;
  }
  if (query.nextToken !== undefined) out.nextToken = query.nextToken;

  if (errors.length) throw new ValidationError(errors);
  return out;
}

module.exports = { validateTaskInput, validateId, validateListQuery, STATUSES, PRIORITIES };
