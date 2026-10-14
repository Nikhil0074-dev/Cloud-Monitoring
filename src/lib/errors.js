'use strict';

class HttpError extends Error {
  constructor(statusCode, code, message, details) {
    super(message);
    this.name = 'HttpError';
    this.statusCode = statusCode;
    this.code = code;
    this.details = details;
  }
}

class ValidationError extends HttpError {
  constructor(details, message = 'Validation failed') {
    super(400, 'VALIDATION_ERROR', message, details);
    this.name = 'ValidationError';
  }
}

class NotFoundError extends HttpError {
  constructor(message = 'Task not found') {
    super(404, 'NOT_FOUND', message);
    this.name = 'NotFoundError';
  }
}

module.exports = { HttpError, ValidationError, NotFoundError };
