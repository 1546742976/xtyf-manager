'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const updates = require('../../clients/frontend/update-runtime.js');
const config = {bridgeVersion:2, capabilities:['ui_updates', 'image_preview']};
const flush = () => new Promise(resolve => setImmediate(resolve));
function fixture(handler = async () => ({status:'current'})) {
  const calls = [], states = [], timers = new Map();
  let time = 100, foreground = true, nextTimer = 0;
  const api = updates.create({
    invoke:async (action, args) => { calls.push({action,args}); return handler(action,args); },
    visible:() => foreground, changed:value => states.push(value), now:() => time,
    setTimer:(callback, delay) => { const id = ++nextTimer; timers.set(id,{callback,delay}); return id; },
    clearTimer:id => timers.delete(id),
  });
  return {api,calls,states,timers,advance:ms => { time += ms; },foreground:value => { foreground = value; api.visibility(); }};
}
test('legacy bridge remains supported without issuing unsupported update calls', async () => {
  const f = fixture(); f.api.configure({version:'1.0.10'}); await f.api.check();
  assert.equal(f.calls.length,0);
  assert.equal(updates.supports(config, 'image_preview'),true);
  assert.equal(updates.supports({...config,bridgeVersion:1}, 'image_preview'),false);
});
test('protocol version and compatible server range are checked independently', () => {
  assert.equal(updates.compatibleServer({protocolVersion:1}),true);
  assert.equal(updates.compatibleServer({protocolVersion:1,uiCompatibility:{protocolMin:1,protocolMax:2}}),true);
  for (const value of [{protocolVersion:2},{protocolVersion:1,uiCompatibility:{protocolMin:2,protocolMax:3}},
    {protocolVersion:1,uiCompatibility:{protocolMin:'1',protocolMax:1}}]) assert.equal(updates.compatibleServer(value),false);
});
test('startup download stays pending without runtime apply or reload', async () => {
  const f = fixture(async action => action === 'update_status' ? {status:'current',currentVersion:'A'} : {status:'pending',currentVersion:'A',pendingVersion:'B'});
  f.api.configure(config); await flush();
  assert.equal(f.states.at(-1).pendingVersion,'B');
  assert.deepEqual(f.calls.map(call => call.action),['update_status','check_updates']);
  assert.equal([...f.timers.values()][0].delay,30 * 60 * 1000);
});
test('background pauses checks; return after five minutes checks once and shorter returns retain cadence', async () => {
  const f = fixture(); f.api.configure(config); await flush();
  f.foreground(false); assert.equal(f.timers.size,0);
  f.advance(4 * 60 * 1000); f.foreground(true); await flush();
  assert.equal(f.calls.filter(call => call.action === 'check_updates').length,1);
  assert.equal([...f.timers.values()][0].delay,26 * 60 * 1000);
  f.foreground(false); f.advance(2 * 60 * 1000); f.foreground(true); await flush();
  assert.equal(f.calls.filter(call => call.action === 'check_updates').length,2);
});
test('manual retry coalesces in-flight checks and old server replies never replace new state', async () => {
  let finish;
  const f = fixture(async action => action === 'update_status' ? {status:'current'} : new Promise(resolve => {finish = resolve;}));
  f.api.configure(config); f.api.check(true);
  assert.equal(f.calls.filter(call => call.action === 'check_updates').length,1);
  f.api.configure({}); const count = f.states.length;
  finish({status:'pending',pendingVersion:'old-server'}); await flush();
  assert.equal(f.states.length,count); assert.equal(f.timers.size,0);
});
test('manual retry requests quarantine retry and failure preserves a retryable lifecycle', async () => {
  const f = fixture(async action => { if (action === 'check_updates') throw new Error('离线'); return {status:'current'}; });
  f.api.configure(config); await flush(); await f.api.check(true);
  assert.equal(f.calls.at(-1).args.retry,true);
  assert.equal(f.states.at(-1).status,'failed');
  assert.equal(f.timers.size,1); f.api.dispose(); assert.equal(f.timers.size,0);
});
