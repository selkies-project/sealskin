/**
 * The GPUs a launch can ask for. On a single node they are its own GPUs, by
 * device. In a cluster they are the GPUs of every answering node the launch
 * may start on: `auto:<kind>` asks for one of that kind on whichever node
 * has one free, and `<device>@<node id>` for that device, which starts the
 * session on its node.
 */

/** How each kind of GPU is named. */
// DRI3 serves Intel's and AMD's GPUs, and the other Mesa drivers, the same way.
const KIND_NAMES = { nvidia: 'NVIDIA', dri3: 'Intel/AMD' };

/** A choice of a kind of GPU, `auto` or `auto:<kind>`, apart from a device whose name starts the same. */
const BY_KIND = /^auto(:|$)/;

const kindOf = (gpu) => gpu.type || (gpu.driver === 'nvidia' ? 'nvidia' : 'dri3');
const supports = (app, kind) => (kind === 'nvidia' ? app.nvidia_support : app.dri3_support);
/**
 * How a GPU is named to people: its model and render node, or its render node and driver.
 *
 * @param {{device: string, driver?: string, name?: string, model?: string}} gpu
 * @returns {string}
 */
export const gpuLabel = (gpu) => {
  const node = gpu.device.split('/').pop();
  const model = gpu.name || gpu.model;
  // The render node stays in the label, which tells two GPUs of one model apart.
  if (model) return `${model} (${node})`;
  return `${node}${gpu.driver ? ` (${gpu.driver})` : ''}`;
};

/**
 * The GPU choices for a launch of `app`, as `[value, label]` pairs, without the choice of none.
 *
 * @param {function} t The translator.
 * @param {object} app The application.
 * @param {object[]} gpus This node's GPUs, as `/api/admin/status` lists them.
 * @param {object|null} cluster The `/api/cluster` answer, null outside a cluster.
 * @param {Set<string>|null} nodes The nodes the launch may start on, from `nodesFor`; null for any.
 * @returns {Array<[string, string]>}
 */
export function gpuChoices(t, app, gpus, cluster, nodes = null) {
  if (!cluster) return gpus.filter((gpu) => supports(app, kindOf(gpu))).map((gpu) => [gpu.device, gpuLabel(gpu)]);
  const held = [];
  cluster.nodes
    .filter((node) => node.alive && (!nodes || nodes.has(node.id)))
    .forEach((node) => (node.gpus || [])
      // A node that does not tell the kind of its GPUs can only be reached by `auto`.
      .filter((gpu) => gpu.type && supports(app, gpu.type))
      .forEach((gpu) => held.push({ node, gpu })));
  const kinds = Object.keys(KIND_NAMES).filter((kind) => held.some((h) => h.gpu.type === kind));
  return [
    ...kinds.map((kind) => [`auto:${kind}`, t('popup.launchView.anyGpu', { kind: KIND_NAMES[kind] })]),
    ...held.map(({ node, gpu }) => [
      `${gpu.device}@${node.id}`,
      t('popup.launchView.gpuOnNode', { gpu: gpuLabel(gpu), node: node.name || node.id }),
    ]),
  ];
}

/**
 * What a GPU choice adds to a launch request: `selected_gpu`, and the `node` a device of a cluster node fixes.
 *
 * @param {string|null} choice A value of `gpuChoices`, or null for none.
 * @returns {{selected_gpu: string|null, node?: string}}
 */
export function gpuRequest(choice) {
  if (!choice) return { selected_gpu: null };
  const at = choice.lastIndexOf('@');
  if (at < 0 || BY_KIND.test(choice)) return { selected_gpu: choice };
  return { selected_gpu: choice.slice(0, at), node: choice.slice(at + 1) };
}
