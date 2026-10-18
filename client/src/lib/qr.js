/**
 * QR code encoder (ISO/IEC 18004, byte mode) with an SVG renderer.
 *
 * The server holds every file a page needs, so the symbol is made here
 * rather than fetched from a third party. Any text is encoded as UTF-8 in
 * the smallest version that holds it at the asked error correction level,
 * with the mask that scores best under the standard's penalty rules.
 *
 * Usage:
 *   import { qrSvg } from '../lib/qr.js';
 *   element.innerHTML = qrSvg('ssk_…@sealskin.example.com:8443');
 */

const ECC = {
  L: { ordinal: 0, formatBits: 1 },
  M: { ordinal: 1, formatBits: 0 },
  Q: { ordinal: 2, formatBits: 3 },
  H: { ordinal: 3, formatBits: 2 },
};

// Error correction codewords per block, then blocks per symbol, by level (L, M, Q, H) and version 1..40.
const ECC_CODEWORDS_PER_BLOCK = [
  [-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
  [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
  [-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
  [-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
];
const NUM_ERROR_CORRECTION_BLOCKS = [
  [-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
  [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],
  [-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68],
  [-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81],
];

const MIN_VERSION = 1;
const MAX_VERSION = 40;
const PENALTY_N1 = 3;
const PENALTY_N2 = 3;
const PENALTY_N3 = 40;
const PENALTY_N4 = 10;

/** Modules available to data and error correction in a version, all function patterns taken out. */
function rawDataModules(version) {
  let result = (16 * version + 128) * version + 64;
  if (version >= 2) {
    const numAlign = Math.floor(version / 7) + 2;
    result -= (25 * numAlign - 10) * numAlign - 55;
    if (version >= 7) result -= 36;
  }
  return result;
}

function dataCodewords(version, level) {
  return Math.floor(rawDataModules(version) / 8)
    - ECC_CODEWORDS_PER_BLOCK[level.ordinal][version] * NUM_ERROR_CORRECTION_BLOCKS[level.ordinal][version];
}

/** Bits of the byte-mode character count field in a version. */
function countBits(version) {
  return version <= 9 ? 8 : 16;
}

function utf8Bytes(text) {
  return Array.from(new TextEncoder().encode(text));
}

function appendBits(bits, value, length) {
  for (let i = length - 1; i >= 0; i--) bits.push((value >>> i) & 1);
}

// --- Reed-Solomon over GF(2^8) with the QR polynomial 0x11D ---

function gfMultiply(x, y) {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11D);
    z ^= ((y >>> i) & 1) * x;
  }
  return z & 0xFF;
}

function rsDivisor(degree) {
  const result = new Array(degree - 1).fill(0).concat([1]);
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < result.length; j++) {
      result[j] = gfMultiply(result[j], root);
      if (j + 1 < result.length) result[j] ^= result[j + 1];
    }
    root = gfMultiply(root, 0x02);
  }
  return result;
}

function rsRemainder(data, divisor) {
  const result = divisor.map(() => 0);
  for (const b of data) {
    const factor = b ^ result.shift();
    result.push(0);
    divisor.forEach((coef, i) => { result[i] ^= gfMultiply(coef, factor); });
  }
  return result;
}

// --- Codewords ---

/** The data bit stream of `bytes` for a version and level, padded to the version's data capacity. */
function dataBits(bytes, version, level) {
  const bits = [];
  appendBits(bits, 0x4, 4);
  appendBits(bits, bytes.length, countBits(version));
  for (const b of bytes) appendBits(bits, b, 8);
  const capacity = dataCodewords(version, level) * 8;
  appendBits(bits, 0, Math.min(4, capacity - bits.length));
  if (bits.length % 8 !== 0) appendBits(bits, 0, 8 - (bits.length % 8));
  for (let pad = 0xEC; bits.length < capacity; pad ^= 0xEC ^ 0x11) appendBits(bits, pad, 8);
  const codewords = new Array(bits.length / 8).fill(0);
  bits.forEach((bit, i) => { codewords[i >>> 3] |= bit << (7 - (i & 7)); });
  return codewords;
}

/** Split the data codewords into blocks, append each block's error correction, and interleave them. */
function interleave(data, version, level) {
  const numBlocks = NUM_ERROR_CORRECTION_BLOCKS[level.ordinal][version];
  const blockEccLen = ECC_CODEWORDS_PER_BLOCK[level.ordinal][version];
  const rawCodewords = Math.floor(rawDataModules(version) / 8);
  const numShortBlocks = numBlocks - (rawCodewords % numBlocks);
  const shortBlockLen = Math.floor(rawCodewords / numBlocks);
  const divisor = rsDivisor(blockEccLen);
  const blocks = [];
  for (let i = 0, k = 0; i < numBlocks; i++) {
    const length = shortBlockLen - blockEccLen + (i < numShortBlocks ? 0 : 1);
    const block = data.slice(k, k + length);
    k += length;
    const ecc = rsRemainder(block, divisor);
    // A short block takes a placeholder so every block indexes alike; it is skipped below.
    if (i < numShortBlocks) block.push(0);
    blocks.push(block.concat(ecc));
  }
  const result = [];
  for (let i = 0; i < blocks[0].length; i++) {
    blocks.forEach((block, j) => {
      if (i !== shortBlockLen - blockEccLen || j >= numShortBlocks) result.push(block[i]);
    });
  }
  return result;
}

// --- The symbol ---

function alignmentPositions(version) {
  if (version === 1) return [];
  const numAlign = Math.floor(version / 7) + 2;
  const size = version * 4 + 17;
  const step = version === 32 ? 26 : Math.ceil((version * 4 + 4) / (numAlign * 2 - 2)) * 2;
  const result = [6];
  for (let pos = size - 7; result.length < numAlign; pos -= step) result.splice(1, 0, pos);
  return result;
}

class QrSymbol {
  constructor(version, level) {
    this.version = version;
    this.level = level;
    this.size = version * 4 + 17;
    this.modules = Array.from({ length: this.size }, () => new Array(this.size).fill(false));
    this.isFunction = Array.from({ length: this.size }, () => new Array(this.size).fill(false));
  }

  setFunction(x, y, dark) {
    this.modules[y][x] = dark;
    this.isFunction[y][x] = true;
  }

  drawFunctionPatterns() {
    for (let i = 0; i < this.size; i++) {
      this.setFunction(6, i, i % 2 === 0);
      this.setFunction(i, 6, i % 2 === 0);
    }
    this.drawFinder(3, 3);
    this.drawFinder(this.size - 4, 3);
    this.drawFinder(3, this.size - 4);
    const positions = alignmentPositions(this.version);
    const last = positions.length - 1;
    positions.forEach((cx, i) => positions.forEach((cy, j) => {
      // The three alignment spots under the finders stay empty.
      if ((i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0)) return;
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) this.setFunction(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
      }
    }));
    this.drawFormatBits(0);
    this.drawVersion();
  }

  drawFinder(cx, cy) {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const dist = Math.max(Math.abs(dx), Math.abs(dy));
        const x = cx + dx;
        const y = cy + dy;
        if (x >= 0 && x < this.size && y >= 0 && y < this.size) this.setFunction(x, y, dist !== 2 && dist !== 4);
      }
    }
  }

  drawFormatBits(mask) {
    const data = (this.level.formatBits << 3) | mask;
    let rem = data;
    for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
    const bits = ((data << 10) | rem) ^ 0x5412;
    const bit = (i) => ((bits >>> i) & 1) === 1;
    for (let i = 0; i <= 5; i++) this.setFunction(8, i, bit(i));
    this.setFunction(8, 7, bit(6));
    this.setFunction(8, 8, bit(7));
    this.setFunction(7, 8, bit(8));
    for (let i = 9; i < 15; i++) this.setFunction(14 - i, 8, bit(i));
    for (let i = 0; i < 8; i++) this.setFunction(this.size - 1 - i, 8, bit(i));
    for (let i = 8; i < 15; i++) this.setFunction(8, this.size - 15 + i, bit(i));
    this.setFunction(8, this.size - 8, true);
  }

  drawVersion() {
    if (this.version < 7) return;
    let rem = this.version;
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1F25);
    const bits = (this.version << 12) | rem;
    for (let i = 0; i < 18; i++) {
      const bit = ((bits >>> i) & 1) === 1;
      const a = this.size - 11 + (i % 3);
      const b = Math.floor(i / 3);
      this.setFunction(a, b, bit);
      this.setFunction(b, a, bit);
    }
  }

  /** Place the codewords in the zigzag the standard prescribes, skipping the function modules. */
  drawCodewords(codewords) {
    let i = 0;
    for (let right = this.size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (let vert = 0; vert < this.size; vert++) {
        for (let j = 0; j < 2; j++) {
          const x = right - j;
          const upward = ((right + 1) & 2) === 0;
          const y = upward ? this.size - 1 - vert : vert;
          if (!this.isFunction[y][x] && i < codewords.length * 8) {
            this.modules[y][x] = ((codewords[i >>> 3] >>> (7 - (i & 7))) & 1) === 1;
            i++;
          }
        }
      }
    }
  }

  /** XOR the data modules with a mask pattern; applying the same mask twice removes it. */
  applyMask(mask) {
    for (let y = 0; y < this.size; y++) {
      for (let x = 0; x < this.size; x++) {
        let invert;
        switch (mask) {
          case 0: invert = (x + y) % 2 === 0; break;
          case 1: invert = y % 2 === 0; break;
          case 2: invert = x % 3 === 0; break;
          case 3: invert = (x + y) % 3 === 0; break;
          case 4: invert = (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0; break;
          case 5: invert = ((x * y) % 2) + ((x * y) % 3) === 0; break;
          case 6: invert = (((x * y) % 2) + ((x * y) % 3)) % 2 === 0; break;
          default: invert = (((x + y) % 2) + ((x * y) % 3)) % 2 === 0; break;
        }
        if (!this.isFunction[y][x] && invert) this.modules[y][x] = !this.modules[y][x];
      }
    }
  }

  /** The standard's penalty score of the symbol as masked now; lower reads better. */
  penalty() {
    let result = 0;
    const lines = [];
    for (let i = 0; i < this.size; i++) {
      lines.push(this.modules[i]);
      lines.push(this.modules.map((row) => row[i]));
    }
    for (const line of lines) {
      let run = 1;
      for (let i = 1; i <= this.size; i++) {
        if (i < this.size && line[i] === line[i - 1]) {
          run++;
          continue;
        }
        if (run >= 5) result += PENALTY_N1 + run - 5;
        run = 1;
      }
      for (let i = 0; i + 7 <= this.size; i++) {
        if (!(line[i] && !line[i + 1] && line[i + 2] && line[i + 3] && line[i + 4] && !line[i + 5] && line[i + 6])) continue;
        const lightBefore = i >= 4 && !line[i - 1] && !line[i - 2] && !line[i - 3] && !line[i - 4];
        const lightAfter = i + 10 < this.size && !line[i + 7] && !line[i + 8] && !line[i + 9] && !line[i + 10];
        if (lightBefore || lightAfter) result += PENALTY_N3;
      }
    }
    for (let y = 0; y < this.size - 1; y++) {
      for (let x = 0; x < this.size - 1; x++) {
        const color = this.modules[y][x];
        if (color === this.modules[y][x + 1] && color === this.modules[y + 1][x] && color === this.modules[y + 1][x + 1]) result += PENALTY_N2;
      }
    }
    const dark = this.modules.reduce((sum, row) => sum + row.filter(Boolean).length, 0);
    const total = this.size * this.size;
    const k = Math.ceil(Math.abs(dark * 20 - total * 10) / total) - 1;
    return result + Math.max(k, 0) * PENALTY_N4;
  }
}

/**
 * Encode text as the modules of a QR symbol.
 *
 * @param {string} text Any text; it is encoded as UTF-8.
 * @param {'L'|'M'|'Q'|'H'} [ecc] Error correction level; M recovers from 15% damage.
 * @returns {{size: number, modules: boolean[][]}} Dark modules are true, indexed `[y][x]`.
 * @throws {Error} When the text does not fit the largest symbol at that level.
 */
export function qrModules(text, ecc = 'M') {
  const level = ECC[ecc] || ECC.M;
  const bytes = utf8Bytes(text);
  let version = MIN_VERSION;
  while (dataCodewords(version, level) * 8 < 4 + countBits(version) + bytes.length * 8) {
    if (version >= MAX_VERSION) throw new Error('The text is too long for a QR code.');
    version++;
  }
  const symbol = new QrSymbol(version, level);
  symbol.drawFunctionPatterns();
  symbol.drawCodewords(interleave(dataBits(bytes, version, level), version, level));
  let best = 0;
  let bestPenalty = Infinity;
  for (let mask = 0; mask < 8; mask++) {
    symbol.applyMask(mask);
    symbol.drawFormatBits(mask);
    const penalty = symbol.penalty();
    if (penalty < bestPenalty) {
      best = mask;
      bestPenalty = penalty;
    }
    symbol.applyMask(mask);
  }
  symbol.applyMask(best);
  symbol.drawFormatBits(best);
  return { size: symbol.size, modules: symbol.modules };
}

/**
 * Render text as an SVG QR code that scales to its container.
 *
 * The symbol sits on a light field with the quiet zone the standard asks for,
 * so it scans from a dark page as well.
 *
 * @param {string} text Any text; it is encoded as UTF-8.
 * @param {object} [options]
 * @param {'L'|'M'|'Q'|'H'} [options.ecc] Error correction level, M by default.
 * @param {number} [options.quiet] Quiet zone in modules, 4 by default.
 * @param {string} [options.label] Accessible name of the image.
 * @returns {string} The `<svg>` markup.
 */
export function qrSvg(text, { ecc = 'M', quiet = 4, label = '' } = {}) {
  const { size, modules } = qrModules(text, ecc);
  const span = size + quiet * 2;
  const parts = [];
  modules.forEach((row, y) => row.forEach((dark, x) => {
    if (dark) parts.push(`M${x + quiet} ${y + quiet}h1v1h-1z`);
  }));
  const title = label ? `<title>${label.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))}</title>` : '';
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${span} ${span}" role="img" shape-rendering="crispEdges">${title}`
    + `<rect width="${span}" height="${span}" fill="#fff"/><path d="${parts.join('')}" fill="#000"/></svg>`;
}
