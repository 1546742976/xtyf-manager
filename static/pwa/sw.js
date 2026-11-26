/* Only this generated, exact public allowlist may enter Cache Storage. */
'use strict';
const CONFIG = __PWA_CONFIG__;
const PREFIX = 'xtyf-pwa-public-';
const CACHE = PREFIX + CONFIG.version;
const OFFLINE = '/static/pwa/offline.html';
const PAGES = new Set(['/', '/login', '/account', '/tasks', '/manage', '/members',
  '/assets', '/assets/detail', '/funds/', '/invoices/', '/discussions', '/discussions/', '/updates', '/admin/security']);
const resourceURL = path => path + '?v=' + CONFIG.version;

function publicPath(request) {
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin ||
      request.headers.has('Range') || request.headers.has('Authorization') || !Object.hasOwn(CONFIG.resources, url.pathname) ||
      url.search !== '?v=' + CONFIG.version) return null;
  return url.pathname;
}

async function verified(response, path) {
  const expected = CONFIG.resources[path];
  const mime = (response?.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
  if (!response || response.status !== 200 || response.type === 'opaque' || response.redirected ||
      response.headers.has('Set-Cookie') || response.headers.has('X-CSRF-Token') ||
      /(?:no-store|private)/i.test(response.headers.get('Cache-Control') || '') ||
      response.headers.get('Vary')?.split(',').some(value => ['*', 'cookie', 'authorization'].includes(value.trim().toLowerCase())) ||
      (mime !== expected.type && !(expected.type === 'text/javascript' && mime === 'application/javascript'))) return false;
  const length = response.headers.get('Content-Length');
  // Fetch decodes gzip/br; Content-Length may describe the compressed transfer.
  if (length !== null && !response.headers.has('Content-Encoding') && Number(length) !== expected.size) return false;
  const reader = response.clone().body?.getReader();
  if (!reader) return false;
  const chunks = [];
  let size = 0;
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > expected.size) { reader.cancel().catch(() => {}); return false; }
    chunks.push(value);
  }
  if (size !== expected.size) return false;
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('') === expected.sha256;
}

function fetchPublic(path) {
  // Never send session cookies to this public-resource-only cache path.
  return fetch(resourceURL(path), {credentials: 'omit', cache: 'no-cache', redirect: 'error'});
}

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    try {
      const responses = [];
      for (const path of Object.keys(CONFIG.resources)) {
        const response = await fetchPublic(path);
        if (!await verified(response, path)) throw new Error('Invalid public install resource');
        responses.push([path, response]);
      }
      const cache = await caches.open(CACHE);
      for (const [path, response] of responses) await cache.put(resourceURL(path), response);
    } catch (error) {
      await caches.delete(CACHE);
      throw error;
    }
    // No skipWaiting: an upgrade must not replace a worker under an open draft.
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name.startsWith(PREFIX) && name !== CACHE) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

async function navigation(request) {
  try {
    // Dynamic pages always hit the server, including history/session checks.
    // HTTP errors and redirects retain their original meaning.
    return await fetch(request, {cache: 'no-store'});
  } catch (error) {
    const response = await caches.match(resourceURL(OFFLINE), {cacheName: CACHE});
    if (await verified(response, OFFLINE)) return response;
    // Browser's offline error is safer than an old private page.
    throw error;
  }
}

async function publicResource(path) {
  try {
    return await fetchPublic(path);
  } catch (error) {
    // Runtime is read-only. In-flight old requests can never recreate a cache
    // removed by an upgrade/retirement. The entire tiny allowlist is installed
    // atomically; no API, business script or large PDF/scanner bundle is here.
    const response = await caches.match(resourceURL(path), {cacheName: CACHE});
    if (await verified(response, path)) return response;
    throw error;
  }
}

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  // Navigation fallback only. Never turn an API/download failure into HTML.
  if (request.mode === 'navigate' && request.destination === 'document' && PAGES.has(url.pathname)) {
    event.respondWith(navigation(request));
    return;
  }
  const path = publicPath(request);
  if (path) event.respondWith(publicResource(path));
});
