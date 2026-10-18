'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const connection = require('../../clients/frontend/connection-runtime.js');
const updates = require('../../clients/frontend/update-runtime.js');
const classify = (status, body) => connection.classify({status, body}, updates.compatibleServer);

test('compatible bootstrap keeps local UI independently of authentication', () => {
  for (const authenticated of [false, true]) {
    const data = {ok:true, protocolVersion:1, authenticated};
    assert.deepEqual(classify(200, JSON.stringify(data)), {mode:'local',data});
  }
});
test('missing endpoint and successful non-protocol responses select website fallback', () => {
  for (const status of [404,405]) assert.equal(classify(status,'').mode,'website');
  for (const body of ['<html>login</html>','null','[]','{}','broken JSON',
    JSON.stringify({ok:true,protocolVersion:2}),
    JSON.stringify({ok:true,protocolVersion:1,uiCompatibility:{protocolMin:2,protocolMax:2}})]) {
    assert.equal(classify(200,body).mode,'website',body);
  }
  assert.equal(classify(204,'').mode,'website');
});
test('transient and authentication failures never masquerade as missing API', () => {
  for (const status of [0,301,401,403,429,500,502,503,504,NaN]) {
    assert.equal(classify(status,'<html>error</html>').mode,'error',String(status));
  }
  const result = classify(200,JSON.stringify({ok:false,protocolVersion:1,error:'维护中'}));
  assert.equal(result.mode,'error'); assert.equal(result.message,'维护中');
});
