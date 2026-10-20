'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

test('task form retains its inactive helper even when that helper is not a participant', () => {
  for (const allMembers of [[{id: 9, name: '已停用协助人', is_active: 0}], []]) {
    const context = {state: {members: [{id: 1, name: '负责人', is_active: 1}], allMembers},
      taskParticipants: () => [1], esc: value => String(value ?? '')};
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../static/task-modal.js'), 'utf8'), context);
    const members = context.modalMembers({owner_id: 1, blocker_helper_id: 9});
    assert.ok(members.some(member => Number(member.id) === 9));
    const html = context.memberOptions(members, 9);
    assert.match(html, /<option\s+value="9" selected>/);
    assert.doesNotMatch(html, /<option[^>]*disabled[^>]*value="9"/,
      'the existing selected helper must remain included in FormData');
    assert.match(html, /（已停用）/);
  }
});
