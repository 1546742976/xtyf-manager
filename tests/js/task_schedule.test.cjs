"use strict";
const assert = require("node:assert/strict"), test = require("node:test");
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const source = file => fs.readFileSync(path.join(__dirname, "../../static", file), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };
const result = (snapshot = "one") => ({ok:true,algorithm_version:2,base_date:"2026-10-16",project:{id:3,name:"哨兵"},
  rows:[{id:1,eligible:true,needs_duration:true},{id:2,eligible:true,needs_duration:true}],task_ids:[1],durations:{1:3},can_apply:true,changed_count:1,errors:[],warnings:[],snapshot});
function sessions() {
  const context = {BusinessUI: { escape: (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])) }, document:{addEventListener(){}}};
  vm.createContext(context); vm.runInContext(source("task-schedule.js"), context);
  return context.createScheduleSession;
}

test("initial preview delegates default selection to server and accepts normalized IDs", async () => {
  let sent;
  const session = sessions()(3, async (url, body) => { sent={url,body}; return result(); });
  await session.preview();
  assert.equal(sent.url,"/api/projects/3/schedule/preview");
  assert.deepEqual(plain(sent.body),{algorithm_version:2,durations:{}});
  assert.deepEqual(plain(session.draft),{base_date:"2026-10-16",task_ids:[1],durations:{1:3}});
  assert.equal(session.result.snapshot,"one");
  assert.equal(session.error,false);
});

test("editing invalidates immediately, and a late older preview cannot replace the new draft or snapshot", async () => {
  const requests=[];
  const session = sessions()(3,(url,body)=>{const pending=deferred();requests.push({url,body,...pending});return pending.promise;});
  const old=session.preview();
  session.draft.base_date="2026-11-01";session.invalidate();
  assert.equal(session.result,null);assert.equal(session.previewPending,false);
  const latest=session.preview();
  requests[1].resolve({...result("new"),base_date:"2026-11-01"});await latest;
  requests[0].resolve(result("old"));await old;
  assert.equal(session.result.snapshot,"new");assert.equal(session.draft.base_date,"2026-11-01");
  session.invalidate();await session.apply();
  assert.equal(requests.length,2,"stale snapshot must never be submitted");
});

test("closing a pending preview isolates its response from a newly opened session", async () => {
  const oldResponse=deferred(), make=sessions();let renders=0;
  const first=make(3,()=>oldResponse.promise,()=>renders++);
  const old=first.preview();const before=renders;
  assert.equal(first.close(),true);
  const second=make(3,async()=>result("second"));await second.preview();
  oldResponse.resolve(result("first"));await old;
  assert.equal(renders,before);assert.equal(first.result,null);assert.equal(second.result.snapshot,"second");
});

test("save blocks close, duplicate submit and preview until refresh settles", async () => {
  const response=deferred(), refresh=deferred();let calls=0,refreshCalls=0;
  const session=sessions()(3,async(url)=>{if(url.endsWith("preview"))return result();calls++;return response.promise;},()=>{},()=>{refreshCalls++;return refresh.promise;});
  await session.preview();const save=session.apply();
  assert.equal(session.saving,true);assert.equal(session.close(),false);
  await session.apply();await session.preview();assert.equal(calls,1);
  response.resolve({ok:true,changed_count:1});await new Promise(setImmediate);
  assert.equal(refreshCalls,1);assert.equal(session.saving,true);assert.equal(session.close(),false);
  refresh.resolve();await save;
  assert.equal(session.saving,false);assert.equal(session.result,null);assert.equal(session.success,true);assert.equal(session.close(),true);
});

test("conflict preserves selection and duration; only a new preview can produce another save", async () => {
  const saves=[];let previews=0;
  const session=sessions()(3,async(url,body)=>{
    if(url.endsWith("preview")){previews++;return {...result("snapshot-"+previews),task_ids:body.task_ids||[1],durations:body.durations};}
    saves.push(body);return saves.length===1?{ok:false,httpStatus:409,error:"前置已变更"}:{ok:true,changed_count:1};
  });
  session.draft={base_date:"2026-10-16",task_ids:[1,2],durations:{2:"4"}};
  await session.preview();await session.apply();
  assert.deepEqual(plain(session.draft.task_ids),[1,2]);assert.equal(session.draft.durations[2],4);
  assert.equal(session.result,null);assert.match(session.message,/前置已变更.*当前输入已保留/);
  assert.equal(session.error,true);
  await session.apply();assert.equal(saves.length,1);
  await session.preview();await session.apply();
  assert.deepEqual(plain(saves),[{snapshot:"snapshot-1"},{snapshot:"snapshot-2"}]);
});

test("server normalization drops supplemental duration once task dates are complete", async () => {
  const session=sessions()(3,async()=>({...result(),rows:[{id:1,eligible:true,needs_duration:false},{id:2,eligible:false,needs_duration:true}],durations:{}}));
  session.draft.durations={1:"4",2:"5",3:"6"};await session.preview();
  assert.deepEqual(plain(session.draft.durations),{});
});

test("network failure retains displayed tasks and draft, invalid duration is rejected before request", async () => {
  let fail=false,calls=0;
  const session=sessions()(3,async()=>{calls++;if(fail)throw Error("offline");return result();});
  await session.preview();fail=true;
  session.draft.durations[1]="7";session.invalidate();await session.preview();
  assert.equal(session.display.rows.length,2);assert.equal(session.draft.durations[1],"7");assert.equal(session.result,null);assert.match(session.message,/offline.*已保留/);
  for(const value of ["-1","0","1.5","1e2","9007199254740992"]){session.draft.durations[1]=value;await session.preview();}
  assert.equal(calls,2);
});

test("dialog retries an initial failed preview without a date, but validates a subsequently cleared date", async () => {
  const controls=new Map(), requests=[];
  function control(selector) {
    if(!controls.has(selector)) controls.set(selector,{value:"",events:{},classList:{toggle(){}},
      setAttribute(){},addEventListener(name,fn){this.events[name]=fn;},
      reportValidity(){this.validityChecks=(this.validityChecks||0)+1;return !!this.value;}});
    return controls.get(selector);
  }
  const root={querySelector:control,querySelectorAll:()=>[],setAttribute(){}};
  const context={BusinessUI: { escape: (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])) }, document:{addEventListener(){},querySelector:()=>root},isManagement:()=>true,
    showModal:()=>true,hideModal(){},refresh:async()=>{},
    apiRequest:async(url,body)=>{requests.push({url,body});return requests.length===1
      ? {ok:false,error:"offline"} : {...result(),rows:[],task_ids:[],durations:{},can_apply:false,changed_count:0};}};
  vm.createContext(context);vm.runInContext(source("task-schedule.js"),context);
  await context.openSchedulePreview(3);
  const date=control("#schedule-base-date"), retry=control("#schedule-repreview");
  assert.equal(date.value,"");
  retry.events.click();await new Promise(setImmediate);
  assert.equal(requests.length,2);assert.equal(requests[1].body.base_date,undefined);
  assert.equal(date.validityChecks,undefined);assert.equal(date.value,"2026-10-16");
  date.value="";date.events.input();retry.events.click();await new Promise(setImmediate);
  assert.equal(date.validityChecks,1);assert.equal(requests.length,2,"cleared date must block a later preview");
});

function modalFixture() {
  function classes(initial=[]) { const values=new Set(initial);return {add:x=>values.add(x),remove:x=>values.delete(x),contains:x=>values.has(x)}; }
  const events={},mask={classList:classes(["hidden"]),events:{},addEventListener(name,fn){this.events[name]=fn;}},button={focus(){}};
  const modal={innerHTML:"",classList:classes(),setAttribute(){},querySelector(){return button;},replaceChildren(){this.innerHTML="";}};
  // Model the DOM className setter, which synchronizes classList in a browser.
  let className = "";
  Object.defineProperty(modal, "className", {get(){return className;},set(value){className=String(value??"");modal.classList=classes(className.split(/\s+/).filter(Boolean));}});
  const document={body:{classList:classes()},activeElement:button,getElementById(id){return {modal,"modal-mask":mask}[id]||null;},addEventListener(name,fn){events[name]=fn;}};
  const context={BusinessUI: { escape: (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])) }, document};vm.createContext(context);vm.runInContext(source("ui-dialog.js"),context);events.DOMContentLoaded();
  return {context,modal,mask,events};
}

test("shared modal guard prevents Escape, backdrop, hide and replacement during save, then releases normally", () => {
  const {context,modal,mask,events}=modalFixture();let saving=true,closed=0;
  assert.equal(context.showModal("original","排期",{beforeClose:()=>!saving,onClose:()=>closed++,className:"schedule-dialog"}),true);
  assert.equal(context.hideModal(),false);
  events.keydown({key:"Escape",preventDefault(){}});
  mask.events.click({target:mask});
  assert.equal(context.showModal("replacement"),false);
  assert.equal(modal.innerHTML,"original");assert.equal(closed,0);assert.equal(mask.classList.contains("hidden"),false);
  saving=false;assert.equal(context.hideModal(),true);assert.equal(closed,1);assert.equal(modal.classList.contains("schedule-dialog"),false);
  assert.equal(context.showModal("new"),true);assert.equal(modal.innerHTML,"new");
});
