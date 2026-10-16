/**
 * A small JSON Schema validator for the schemas Pydantic exports (draft 2020-12 subset).
 *
 * It covers exactly the keywords those schemas use — type, properties, required,
 * additionalProperties, propertyNames, items, prefixItems, minItems, maxItems, uniqueItems,
 * enum, const, anyOf, oneOf with a discriminator, allOf, $ref into $defs, format (date,
 * date-time) — and reports every violation with its JSON path. The overlay uses it to check an
 * imported review package before loading it; the tests use it to hold fixtures and reviewer ops
 * to the pipeline's contracts.
 */

export interface JsonSchema {
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
  type?: string | string[];
  properties?: Record<string, JsonSchema>;
  required?: string[];
  additionalProperties?: boolean | JsonSchema;
  propertyNames?: JsonSchema;
  items?: JsonSchema | false;
  prefixItems?: JsonSchema[];
  minItems?: number;
  maxItems?: number;
  uniqueItems?: boolean;
  enum?: unknown[];
  const?: unknown;
  anyOf?: JsonSchema[];
  oneOf?: JsonSchema[];
  allOf?: JsonSchema[];
  discriminator?: { propertyName: string; mapping?: Record<string, string> };
  format?: string;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  [key: string]: unknown;
}

export interface SchemaError {
  path: string;
  message: string;
}

const typeOf = (v: unknown): string => {
  if (v === null) return 'null';
  if (Array.isArray(v)) return 'array';
  if (typeof v === 'number') return Number.isInteger(v) ? 'integer' : 'number';
  return typeof v;
};

const matchesType = (v: unknown, t: string): boolean => {
  const actual = typeOf(v);
  if (t === 'number') return actual === 'number' || actual === 'integer';
  return actual === t;
};

const equal = (a: unknown, b: unknown): boolean => {
  if (a === b) return true;
  if (typeof a === 'number' && typeof b === 'number') return a === b;
  return JSON.stringify(a) === JSON.stringify(b);
};

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const DATE_TIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/i;

const resolveRef = (root: JsonSchema, ref: string): JsonSchema => {
  if (!ref.startsWith('#/')) throw new RangeError(`only local $refs are supported: ${ref}`);
  let node: unknown = root;
  for (const part of ref.slice(2).split('/')) {
    node = (node as Record<string, unknown>)?.[part.replace(/~1/g, '/').replace(/~0/g, '~')];
    if (node === undefined) throw new RangeError(`unresolved $ref ${ref}`);
  }
  return node as JsonSchema;
};

const at = (path: string, key: string | number) => (typeof key === 'number' ? `${path}[${key}]` : /^[A-Za-z_$][\w$]*$/.test(key) ? `${path}.${key}` : `${path}[${JSON.stringify(key)}]`);

const check = (root: JsonSchema, schema: JsonSchema | boolean, value: unknown, path: string, errors: SchemaError[]): void => {
  if (schema === true) return;
  if (schema === false) {
    errors.push({ path, message: 'no value is allowed here' });
    return;
  }
  if (schema.$ref) {
    check(root, resolveRef(root, schema.$ref), value, path, errors);
    // Siblings of $ref (titles, defaults) carry no constraints in Pydantic's output; keep checking any that do.
  }
  if (schema.type !== undefined) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (!types.some((t) => matchesType(value, t))) {
      errors.push({ path, message: `expected ${types.join(' or ')}, got ${typeOf(value)}` });
      return;
    }
  }
  if (schema.const !== undefined && !equal(value, schema.const)) errors.push({ path, message: `must be ${JSON.stringify(schema.const)}` });
  if (schema.enum && !schema.enum.some((e) => equal(e, value))) errors.push({ path, message: `must be one of ${schema.enum.map((e) => JSON.stringify(e)).join(', ')}` });

  if (typeof value === 'string') {
    if (schema.format === 'date' && !DATE.test(value)) errors.push({ path, message: 'must be a date (YYYY-MM-DD)' });
    if (schema.format === 'date-time' && !DATE_TIME.test(value)) errors.push({ path, message: 'must be a date-time (ISO 8601)' });
    if (schema.minLength !== undefined && value.length < schema.minLength) errors.push({ path, message: `must be at least ${schema.minLength} characters` });
    if (schema.maxLength !== undefined && value.length > schema.maxLength) errors.push({ path, message: `must be at most ${schema.maxLength} characters` });
    if (schema.pattern !== undefined && !new RegExp(schema.pattern, 'u').test(value)) errors.push({ path, message: `must match ${schema.pattern}` });
  }
  if (typeof value === 'number') {
    if (schema.minimum !== undefined && value < schema.minimum) errors.push({ path, message: `must be at least ${schema.minimum}` });
    if (schema.maximum !== undefined && value > schema.maximum) errors.push({ path, message: `must be at most ${schema.maximum}` });
    if (schema.exclusiveMinimum !== undefined && value <= schema.exclusiveMinimum) errors.push({ path, message: `must be more than ${schema.exclusiveMinimum}` });
    if (schema.exclusiveMaximum !== undefined && value >= schema.exclusiveMaximum) errors.push({ path, message: `must be less than ${schema.exclusiveMaximum}` });
  }

  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) errors.push({ path, message: `must have at least ${schema.minItems} items` });
    if (schema.maxItems !== undefined && value.length > schema.maxItems) errors.push({ path, message: `must have at most ${schema.maxItems} items` });
    const prefix = schema.prefixItems ?? [];
    prefix.forEach((s, i) => i < value.length && check(root, s, value[i], at(path, i), errors));
    if (schema.items !== undefined) value.slice(prefix.length).forEach((v, i) => check(root, schema.items as JsonSchema | false, v, at(path, i + prefix.length), errors));
    if (schema.uniqueItems) {
      const seen = new Set<string>();
      value.forEach((v, i) => {
        const k = JSON.stringify(v);
        if (seen.has(k)) errors.push({ path: at(path, i), message: 'duplicate item' });
        seen.add(k);
      });
    }
  }

  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    const obj = value as Record<string, unknown>;
    for (const k of schema.required ?? []) if (!(k in obj)) errors.push({ path: at(path, k), message: 'is required' });
    for (const [k, v] of Object.entries(obj)) {
      if (schema.propertyNames) {
        const before = errors.length;
        check(root, schema.propertyNames, k, at(path, k), errors);
        if (errors.length > before) continue;
      }
      const prop = schema.properties?.[k];
      if (prop !== undefined) check(root, prop, v, at(path, k), errors);
      else if (schema.additionalProperties === false) errors.push({ path: at(path, k), message: 'is not allowed' });
      else if (schema.additionalProperties && typeof schema.additionalProperties === 'object') check(root, schema.additionalProperties, v, at(path, k), errors);
    }
  }

  for (const s of schema.allOf ?? []) check(root, s, value, path, errors);

  if (schema.anyOf) {
    const attempts = schema.anyOf.map((s) => {
      const errs: SchemaError[] = [];
      check(root, s, value, path, errs);
      return errs;
    });
    if (!attempts.some((e) => e.length === 0)) errors.push(...closest(attempts, path, 'any'));
  }

  if (schema.oneOf) {
    const disc = schema.discriminator;
    const tag = disc && value && typeof value === 'object' ? (value as Record<string, unknown>)[disc.propertyName] : undefined;
    if (disc?.mapping && typeof tag === 'string') {
      const ref = disc.mapping[tag];
      if (!ref) errors.push({ path: at(path, disc.propertyName), message: `must be one of ${Object.keys(disc.mapping).join(', ')}` });
      else check(root, { $ref: ref }, value, path, errors);
    } else {
      const attempts = schema.oneOf.map((s) => {
        const errs: SchemaError[] = [];
        check(root, s, value, path, errs);
        return errs;
      });
      const passing = attempts.filter((e) => e.length === 0).length;
      if (passing === 0) errors.push(...closest(attempts, path, 'one'));
      else if (passing > 1) errors.push({ path, message: 'matches more than one alternative' });
    }
  }
};

/**
 * When no alternative fits, report the one that came closest, as a reader would want: among the
 * alternatives of the value's own type (an object is not "expected null"), the one with fewest errors.
 */
const closest = (attempts: SchemaError[][], path: string, which: string): SchemaError[] => {
  const wrongType = (errs: SchemaError[]) => errs.some((e) => e.path === path && e.message.startsWith('expected '));
  const sameType = attempts.filter((errs) => !wrongType(errs));
  const pool = sameType.length ? sameType : attempts;
  const best = pool.reduce((a, b) => (b.length < a.length ? b : a));
  return best.length ? best : [{ path, message: `does not match ${which} of the allowed shapes` }];
};

/** Every violation of `schema` by `value` (empty when it validates). `ref` names a definition inside the schema. */
export const validate = (schema: JsonSchema, value: unknown, ref?: string): SchemaError[] => {
  const errors: SchemaError[] = [];
  check(schema, ref ? { $ref: ref } : schema, value, '$', errors);
  return errors;
};

export const describeErrors = (errors: SchemaError[], max = 5): string =>
  errors
    .slice(0, max)
    .map((e) => `${e.path}: ${e.message}`)
    .join('; ') + (errors.length > max ? `; and ${errors.length - max} more` : '');
