"use strict";
const assert=require('node:assert/strict'),test=require('node:test');
const {filesError,readThrough,makeAttempt}=require('../../static/discussions.js');
const MiB=1024*1024;
test('collaboration file limits accept boundaries and reject excess before sending',()=>{
 assert.equal(filesError(Array.from({length:5},()=>({size:1}))), '');
 assert.match(filesError(Array.from({length:6},()=>({size:1}))), /5/);
 assert.equal(filesError([{size:100*MiB},{size:80*MiB}]),'');
 assert.match(filesError([{size:100*MiB+1}]), /100/);
 assert.match(filesError([{size:100*MiB},{size:80*MiB+1}]), /180/);
});
test('read pointer never skips an unseen other-member message',()=>{
 const messages=[1,2,3].map(id=>({id,author_id:2}));
 assert.equal(readThrough(messages,new Set([1,3]),0,false,1),1);
 assert.equal(readThrough(messages,new Set([1,2,3]),0,false,1),3);
});
test('latest history page cannot mark earlier unloaded messages read',()=>{
 const messages=[51,52,53].map(id=>({id,author_id:2}));
 assert.equal(readThrough(messages,new Set([51,52,53]),0,true,1,51),0);
 assert.equal(readThrough(messages,new Set([51,52,53]),51,true,1,51),53);
});
test('loading older history cannot regress read state or clear newer unread messages',()=>{
 const messages=[{id:1,author_id:2},{id:3,author_id:2},{id:4,author_id:2}];
 assert.equal(readThrough(messages,new Set([1]),3,false,1),3);
 assert.equal(readThrough(messages,new Set([1,4]),3,false,1),4);
});
test('own and withdrawn gaps do not block meaningful visible read progress',()=>{
 const messages=[{id:1,author_id:1},{id:2,author_id:2,withdrawn:true},{id:3,author_id:2}];
 assert.equal(readThrough(messages,new Set([3]),0,false,1),3);
 assert.equal(readThrough([{id:1,author_id:1}],new Set([1]),0,false,1),1);
});
test('uncertain submission keeps UUID and the original File references for manual retry',()=>{
 const file={name:'report.pdf',size:4},draft={title:'topic',body:'body',files:[file],attempt:null};
 const first=makeAttempt(draft,()=> 'uuid-first');first.uncertain=true;
 draft.body='edited later';draft.files=[];
 const retry=makeAttempt(draft,()=> 'uuid-second');
 assert.equal(retry,first);assert.equal(retry.id,'uuid-first');assert.equal(retry.body,'body');assert.equal(retry.files[0],file);
});
test('a confirmed failed attempt can be replaced after correcting the draft',()=>{
 const draft={body:'one',files:[],attempt:null};makeAttempt(draft,()=> 'first');draft.attempt=null;draft.body='corrected';
 assert.equal(makeAttempt(draft,()=> 'second').body,'corrected');assert.equal(draft.attempt.id,'second');
});

test('an old withdrawal patch does not prove an unloaded history gap has been read',()=>{
 const messages=[{id:5,author_id:2,withdrawn:true},{id:51,author_id:2},{id:52,author_id:2}];
 assert.equal(readThrough(messages,new Set([51,52]),10,true,1,51),10);
 // Only after actually loading and viewing the intervening history can it advance.
 const history=Array.from({length:42},(_,i)=>({id:i+11,author_id:2}));
 assert.equal(readThrough([{id:10,author_id:2},...history],new Set(history.map(m=>m.id)),10,true,1,10),52);
});
