/**
 * Which home directories a launch may use where it starts. A home directory
 * lives on one node of a cluster, so a launch aimed at a node or a pool can
 * use the homes held there, the cleanroom, and an auto home that does not
 * exist yet or is held there too.
 */

/** The name of the per-app home directory a launch with `auto` uses. */
export const autoHomeName = (appName) => `auto-${appName.toLowerCase().replace(/[\s_]+/g, '-').replace(/[^a-z0-9-]/g, '')}`;

/**
 * The ids of the nodes a `where` choice allows, or null for any node.
 *
 * @param {object|null} cluster The `/api/cluster` answer.
 * @param {string} where `''`, `pool:<name>`, or `node:<id>`.
 * @returns {Set<string>|null}
 */
export function nodesFor(cluster, where) {
  if (!cluster || !where) return null;
  const [kind, name] = where.split(/:(.*)/);
  if (kind === 'node') return new Set([name]);
  if (kind === 'pool') return new Set(cluster.nodes.filter((node) => node.pool === name).map((node) => node.id));
  return null;
}

/**
 * Whether a home choice can start where the launch goes.
 *
 * @param {object|null} cluster The `/api/cluster` answer, null outside a cluster.
 * @param {Set<string>|null} nodes From `nodesFor`.
 * @param {string} choice `auto`, `cleanroom`, or a home directory's name.
 * @param {string} autoName The home `auto` stands for.
 */
export function homeAllowed(cluster, nodes, choice, autoName) {
  if (!cluster || !nodes || choice === 'cleanroom') return true;
  const holder = cluster.homes[choice === 'auto' ? autoName : choice];
  return !holder || nodes.has(holder);
}

/**
 * The home to select among the allowed ones: the one wanted, else the last
 * used, else `auto`, else the cleanroom.
 *
 * @param {function(string): boolean} allowed
 * @param {...string} wanted In order of preference; empty entries are skipped.
 */
export function bestHome(allowed, ...wanted) {
  return [...wanted, 'auto', 'cleanroom'].find((choice) => choice && allowed(choice));
}
