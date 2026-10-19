'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture({native = false, imageURL, isLocal, protocol = 'https:', url = '/api/account/avatar?v=old'} = {}) {
  const images = [];
  const hosts = Array.from({length:3}, () => {
    const initial = {hidden:false};
    return {dataset:{avatarUrl:url}, initial, images:[],
      querySelector(selector) { return selector === '[data-avatar-initial]' ? initial : this.images[0]; },
      append(image) { image.host = this; this.images.push(image); },
      contains(image) { return this.images.includes(image); }};
  });
  let ready;
  const context = {URL, location:{href:protocol+'//team.example/account', origin:protocol+'//team.example'},
    window:native ? {XTYF:{invoke(){}, imageURL, isLocal}} : {},
    Image:class {
      constructor() { this.dataset = {}; this.events = {}; images.push(this); }
      addEventListener(name, listener) { this.events[name] = listener; }
      remove() { this.host.images = this.host.images.filter(image => image !== this); }
    },
    document:{querySelectorAll:() => hosts, addEventListener:(_name, listener) => { ready = listener; }}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../static/avatar.js'), 'utf8'), context);
  ready();
  return {hosts, images, api:context.window.AvatarDisplay};
}

test('all three places switch only after loading and fall back to initials on image errors', () => {
  const {hosts, images, api} = fixture();
  assert.equal(images.length, 3);
  hosts.forEach(host => assert.equal(host.initial.hidden, false));
  images.forEach(image => image.events.load());
  hosts.forEach(host => assert.equal(host.initial.hidden, true));
  api.setUrl('/api/account/avatar?v=new');
  images.slice(3).forEach(image => image.events.error());
  hosts.forEach(host => { assert.equal(host.initial.hidden, false); assert.equal(host.images.length, 0); });
});
test('late loads cannot restore an obsolete avatar after replacement or removal', () => {
  const {hosts, images, api} = fixture();
  const oldImages = images.slice();
  api.setUrl('/api/account/avatar?v=new');
  oldImages.forEach(image => image.events.load());
  hosts.forEach(host => assert.equal(host.initial.hidden, false));
  api.setUrl(null);
  images.forEach(image => image.events.load());
  hosts.forEach(host => { assert.equal(host.initial.hidden, false); assert.equal(host.images.length, 0); });
});
test('only the authenticated own-avatar endpoint is accepted as an image source', () => {
  const {api} = fixture({url:null});
  for (const value of ['https://foreign.example/api/account/avatar', '/api/account/avatar/other',
    'javascript:alert(1)', 'data:image/png;base64,AAA', '/static/secret.png', '', null]) {
    assert.equal(api.safeUrl(value), null);
  }
  assert.equal(api.safeUrl('https://team.example/api/account/avatar?v=abc'), '/api/account/avatar?v=abc');
});
test('native client keeps initials without starting unsupported image requests', () => {
  const {hosts, images, api} = fixture({native:true});
  api.setUrl('/api/account/avatar?v=new');
  assert.equal(images.length, 0);
  hosts.forEach(host => assert.equal(host.initial.hidden, false));
});

test('native avatar uses authenticated host transport and does not expose API src to the webview', async () => {
  const calls=[];
  const {hosts,images}=fixture({native:true,imageURL:async path=>{calls.push(path);return 'blob:private-avatar';}});
  assert.equal(images[0].src,undefined);
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(calls,Array(3).fill('/api/account/avatar?v=old'));
  images.forEach(image=>{assert.equal(image.src,'blob:private-avatar');image.events.load();});
  hosts.forEach(host=>assert.equal(host.initial.hidden,true));
});
test('native stale resource response cannot restore an avatar that was reset', async () => {
  const pending=[];
  const {hosts,images,api}=fixture({native:true,imageURL:()=>new Promise(resolve=>pending.push(resolve))});
  api.setUrl(null); pending.forEach(resolve=>resolve('blob:obsolete'));
  await new Promise(resolve=>setImmediate(resolve));
  images.forEach(image=>assert.equal(image.src,undefined));
  hosts.forEach(host=>{assert.equal(host.initial.hidden,false);assert.equal(host.images.length,0);});
});
test('native custom schemes use protocol and host identity instead of opaque URL origins', async () => {
  const {api,images}=fixture({native:true,protocol:'tauri:',imageURL:async()=>'blob:avatar',
    isLocal:url=>url.protocol==='tauri:'&&url.host==='team.example'});
  assert.equal(api.safeUrl('/api/account/avatar?v=local'),'/api/account/avatar?v=local');
  assert.equal(api.safeUrl('tauri://other-host/api/account/avatar'),null);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(images.length,3);images.forEach(image=>assert.equal(image.src,'blob:avatar'));
});
