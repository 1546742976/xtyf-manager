"""Public install metadata; no business pages or account state belong here."""
import hashlib
import json
import os
from pathlib import Path

from flask import Blueprint, Response, current_app

bp = Blueprint('pwa', __name__)
PUBLIC_ENDPOINTS = frozenset({'pwa.manifest', 'pwa.worker'})
# Deliberately narrow: business JS/CSS, API responses, avatars, PDFs and uploads
# remain network-only. Adding a resource requires reviewing its bytes and MIME.
PUBLIC_FILES = {
    'pwa/offline.html': 'text/html',
    'pwa/icon-192.png': 'image/png',
    'pwa/icon-512.png': 'image/png',
    'pwa/maskable-512.png': 'image/png',
    'pwa/apple-touch-icon.png': 'image/png',
    'pwa.js': 'text/javascript',
    'pwa.css': 'text/css',
}
MAX_FILE_BYTES = 512 * 1024
MAX_CACHE_BYTES = 2 * 1024 * 1024
RETIRE_WORKER = """'use strict';
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil((async () => {
  await self.clients.claim();
  for (const name of await caches.keys()) {
    if (name.startsWith('xtyf-pwa-public-')) await caches.delete(name);
  }
  await self.registration.unregister();
})()));
"""


def build_config(root):
    """Content version works in a git checkout and a HEAD source deployment."""
    root = Path(root)
    resources = {}
    for name, mime in PUBLIC_FILES.items():
        data = (root / 'static' / name).read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise RuntimeError('PWA resource exceeds cache limit: ' + name)
        resources['/static/' + name] = {
            'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'type': mime,
        }
    if sum(item['size'] for item in resources.values()) > MAX_CACHE_BYTES:
        raise RuntimeError('PWA resources exceed total cache limit')
    source = (root / 'static' / 'pwa' / 'sw.js').read_text(encoding='utf-8')
    version = hashlib.sha256((source + json.dumps(resources, sort_keys=True)).encode()).hexdigest()[:24]
    return {'version': version, 'resources': resources}, source


def init_pwa(app):
    config, source = build_config(app.root_path)
    app.extensions['pwa'] = config
    app.extensions['pwa_worker'] = source.replace('__PWA_CONFIG__', json.dumps(config, sort_keys=True))
    app.config['PWA_ENABLED'] = os.environ.get('PROGRESS_PWA_ENABLED', '1').lower() not in {'0', 'false', 'no'}
    app.register_blueprint(bp)
    app.context_processor(lambda: {'pwa_version': app.extensions['pwa']['version'],
                                  'pwa_enabled': app.config['PWA_ENABLED']})


@bp.get('/manifest.webmanifest')
def manifest():
    version = current_app.extensions['pwa']['version']
    data = {
        'id': '/', 'name': '巡天御风管理系统', 'short_name': '巡天御风',
        'lang': 'zh-CN', 'start_url': '/', 'scope': '/', 'display': 'standalone',
        'background_color': '#f5f5f7', 'theme_color': '#f5f5f7',
        'description': '巡天御风团队管理工作台；业务操作需联网。',
        'icons': [
            {'src': f'/static/pwa/icon-{size}.png?v={version}', 'sizes': f'{size}x{size}',
             'type': 'image/png', 'purpose': 'any'} for size in (192, 512)
        ] + [{'src': f'/static/pwa/maskable-512.png?v={version}', 'sizes': '512x512',
              'type': 'image/png', 'purpose': 'maskable'}],
    }
    return Response(json.dumps(data, ensure_ascii=False), content_type='application/manifest+json; charset=utf-8',
                    headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'})


@bp.get('/sw.js')
def worker():
    source = current_app.extensions['pwa_worker'] if current_app.config['PWA_ENABLED'] else RETIRE_WORKER
    return Response(source, content_type='text/javascript; charset=utf-8',
                    headers={'Cache-Control': 'no-cache', 'Service-Worker-Allowed': '/',
                             'X-Content-Type-Options': 'nosniff'})
