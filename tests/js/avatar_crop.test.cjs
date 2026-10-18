'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const crop = require('../../static/account-avatar.js');

test('default crops use the centered square in oriented landscape, portrait and square images', () => {
  assert.deepEqual(crop.rectangle(crop.initial(800, 400)), {x:200, y:0, size:400});
  assert.deepEqual(crop.rectangle(crop.initial(400, 800)), {x:0, y:200, size:400});
  assert.deepEqual(crop.rectangle(crop.initial(401, 401)), {x:0, y:0, size:401});
});
test('zoom preserves the chosen center and clamps zoom-out to the image', () => {
  let state = crop.zoom(crop.initial(800, 400), 4);
  state = crop.move(state, -9999, -9999);
  assert.deepEqual(crop.rectangle(state), {x:0, y:0, size:100});
  assert.deepEqual(crop.rectangle(crop.zoom(state, 1)), {x:0, y:0, size:400});
  assert.equal(crop.zoom(state, 99).scale, 4);
  assert.equal(crop.zoom(state, 0).scale, 1);
});
test('pointer and keyboard movements clamp at every edge without introducing empty pixels', () => {
  const state = crop.zoom(crop.initial(500, 900), 2);
  assert.deepEqual(crop.rectangle(crop.move(state, 1e9, -1e9)), {x:250, y:0, size:250});
  assert.deepEqual(crop.rectangle(crop.move(state, -1e9, 1e9)), {x:0, y:650, size:250});
  assert.deepEqual(crop.rectangle(crop.move(state, 15, -20)), {x:140, y:305, size:250});
});
test('odd dimensions and fractional zoom always submit integer source pixels strictly inside the image', () => {
  for (const [width, height] of [[1,1], [1,10000], [403,877], [10000,1999]]) {
    for (const scale of [1,1.01,1.5,2.99,4]) {
      for (const [dx, dy] of [[0,0], [-123456,-123456], [123456,123456], [25.3,-79.2]]) {
        const result = crop.rectangle(crop.move(crop.zoom(crop.initial(width,height),scale),dx,dy));
        assert.ok(Object.values(result).every(Number.isInteger));
        assert.ok(result.size >= 1 && result.x >= 0 && result.y >= 0);
        assert.ok(result.x + result.size <= width && result.y + result.size <= height);
      }
    }
  }
});
test('invalid dimensions are rejected and invalid pointer/zoom inputs do not corrupt a draft', () => {
  for (const width of [0,-1,1.5,NaN,Infinity]) assert.throws(() => crop.initial(width, 100));
  const state = crop.initial(200, 300);
  assert.equal(crop.zoom(state, NaN), state);
  assert.equal(crop.move(state, Infinity, 0), state);
});
