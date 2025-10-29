function pemToArrayBuffer(pem) {
  const b64 = pem
    .replace(/-----BEGIN (PUBLIC|PRIVATE) KEY-----/, '')
    .replace(/-----END (PUBLIC|PRIVATE) KEY-----/, '')
    .replace(/\s/g, '');
  const binaryStr = atob(b64);
  const len = binaryStr.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binaryStr.charCodeAt(i);
  }
  return bytes.buffer;
}

function arrayBufferToBase64(buffer) {
  let binary = '';
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

function arrayBufferToPem(buffer, type) {
  const b64 = arrayBufferToBase64(buffer);
  const lines = b64.match(/.{1,64}/g).join('\n');
  return `-----BEGIN ${type} KEY-----\n${lines}\n-----END ${type} KEY-----\n`;
}

async function generateRsaKeyPair() {
  const keyPair = await crypto.subtle.generateKey({
    name: 'RSASSA-PKCS1-v1_5',
    modulusLength: 2048,
    publicExponent: new Uint8Array([0x01, 0x00, 0x01]),
    hash: 'SHA-256',
  }, true, ['sign', 'verify']);

  const privateKeyBuffer = await crypto.subtle.exportKey('pkcs8', keyPair.privateKey);
  const publicKeyBuffer = await crypto.subtle.exportKey('spki', keyPair.publicKey);

  return {
    privateKey: arrayBufferToPem(privateKeyBuffer, 'PRIVATE'),
    publicKey: arrayBufferToPem(publicKeyBuffer, 'PUBLIC'),
  };
}

function base64UrlEncode(str) {
  return btoa(str)
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

function arrayBufferToBase64Url(buffer) {
  let binary = '';
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return base64UrlEncode(binary);
}

const keyCache = new Map();

async function generateJwtNative(privateKeyPem, username) {
  if (!privateKeyPem || !username) {
    throw new Error("Private key and username are required to generate a token.");
  }

  let cryptoKey = keyCache.get(privateKeyPem);
  if (!cryptoKey) {
    try {
      const keyBuffer = pemToArrayBuffer(privateKeyPem);
      cryptoKey = await crypto.subtle.importKey(
        'pkcs8',
        keyBuffer, {
          name: 'RSASSA-PKCS1-v1_5',
          hash: 'SHA-256'
        },
        true,
        ['sign']
      );
      keyCache.set(privateKeyPem, cryptoKey);
    } catch (error) {
      console.error("Failed to import private key:", error);
      throw new Error("Invalid private key format. Please ensure it is a valid PKCS#8 PEM key.");
    }
  }

  const header = {
    alg: 'RS256',
    typ: 'JWT'
  };
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    iat: now,
    exp: now + (5 * 60),
    sub: username,
  };

  const encodedHeader = base64UrlEncode(JSON.stringify(header));
  const encodedPayload = base64UrlEncode(JSON.stringify(payload));

  const signingInput = `${encodedHeader}.${encodedPayload}`;
  const signingInputBuffer = new TextEncoder().encode(signingInput);

  const signatureBuffer = await crypto.subtle.sign({
      name: 'RSASSA-PKCS1-v1_5'
    },
    cryptoKey,
    signingInputBuffer
  );

  const encodedSignature = arrayBufferToBase64Url(signatureBuffer);

  return `${signingInput}.${encodedSignature}`;
}
