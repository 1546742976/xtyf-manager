"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const src = fs.readFileSync(path.join(__dirname, "../../static/image-viewer.js"), "utf8");
function harness(fetchResponse) {
  const handlers = {};
  const dialogEvents = {};
  const stageEvents = {};
  const created = [], revoked = [], anchors = [];
  const stage = {
    clientWidth:800, clientHeight:600,
    getBoundingClientRect(){return {left:0,top:0,width:this.clientWidth,height:this.clientHeight};},
    addEventListener(type, handler) {stageEvents[type] = handler;},
    setPointerCapture() {},
  };
  const picture = {
    hidden: true, style: {}, naturalWidth: 10, naturalHeight: 10,
    set src(value) {this._src = value; if (value && this.onload) this.onload();},
    get src() {return this._src;},
    removeAttribute(name) { if (name === "src") this._src = ""; },
  };
  const name = {textContent:""}, dimensions = {textContent:""}, status = {textContent:""}, zoom = {textContent:""};
  const nodes = {"[data-iv-image]":picture,"[data-iv-name]":name,"[data-iv-dimensions]":dimensions,"[data-iv-status]":status,"[data-iv-zoom]":zoom,"[data-iv-stage]":stage};
  const dialog = {
    open: false,
    setAttribute() {},
    addEventListener(type, handler) {dialogEvents[type] = handler;},
    querySelector(selector) {return nodes[selector];},
    showModal() {this.open = true;},
    close() {this.open = false; dialogEvents.close?.();},
  };
  const document = {
    activeElement: {isConnected:true, focus() {}},
    body: {append(node) {created.push(node);}},
    createElement(tag) {
      if (tag === "dialog") return dialog;
      if (tag === "a") {
        const attributes = new Map();
        const link = {
          href:"", download:"", dataset:{}, remove(){}, querySelector(){return null;},
          setAttribute(key, value){attributes.set(key, value);},
          getAttribute(key){return key === "download" ? this.download : attributes.get(key) ?? null;},
          hasAttribute(key){return attributes.has(key);},
          matches(selector){return selector === "a[href]" || (selector === "[data-no-image-preview]" && attributes.has("data-no-image-preview"));},
          click(){
            const event = {button:0, defaultPrevented:false, target:{closest:()=>this},
              preventDefault(){this.defaultPrevented=true;}, stopPropagation(){}};
            handlers.click.handler(event);
            this.clicked = true;
            this.downloadStarted = !event.defaultPrevented;
          },
        };
        anchors.push(link);
        return link;
      }
      if (tag === "canvas") return {width:0,height:0,getContext() {return {drawImage(){}};},toBlob(callback) {callback(new Blob(["png"], {type:"image/png"}));}};
      throw new Error("Unexpected tag: " + tag);
    },
    addEventListener(type, handler, capture) {handlers[type] = {handler, capture};},
  };
  const NativeURL = URL;
  class URLProxy extends NativeURL {}
  URLProxy.createObjectURL = blob => {const token = `blob:unit-test/${created.length}`;created.push(blob);return token;};
  URLProxy.revokeObjectURL = value => revoked.push(value);
  const fetchCalls = [];
  const ctx = {document, location:{href:"https://example.org/tasks", origin:"https://example.org"},
    URL:URLProxy, Blob, AbortController, ClipboardItem:undefined, navigator:{},
    fetch:async (url, options) => {fetchCalls.push({url,options});return fetchResponse;}, console, Uint8Array, Error};
  ctx.window=ctx;
  vm.createContext(ctx); vm.runInContext(src, ctx);
  return {api:ctx.ImageViewer,ctx,handlers,dialog,dialogEvents,stageEvents,stage,picture,name,dimensions,status,zoom,fetchCalls,created,revoked,anchors};
}
const imageBlob = new Blob([Uint8Array.from([137,80,78,71,13,10,26,10,0,0,0,0])], {type:"application/octet-stream"});
const response = blob => ({ok:true, headers:{get(key){return key === "Content-Length" ? String(blob.size) : "application/octet-stream";}}, blob:async()=>blob});
const mockLink = (href, props={}) => ({
  href, dataset:{}, getAttribute(){return null;}, hasAttribute(key){return key === "data-image-preview" ? !!props.explicit : false;},
  matches(selector){return selector === "a[href]";}, querySelector(){return null;},...props,
});

test("global link policy previews same-origin direct images and opted-in API images only", () => {
  const {api} = harness(response(imageBlob));
  assert.equal(api.canPreview("/content/screen.png?x=1"), true);
  assert.equal(api.canPreview("/content/report.pdf"), false);
  assert.equal(api.canPreview("https://evil.example/x.png"), false);
  assert.equal(api.sourceFor(mockLink("https://example.org/task/123", {explicit:true}))?.url,"https://example.org/task/123");
  assert.equal(api.sourceFor(mockLink("https://example.org/task/123")),null);
  assert.equal(api.sourceFor(mockLink("https://example.org/report.csv")),null);
  assert.equal(api.sourceFor(mockLink("https://evil.example/a.png")),null);
});

test("delegated image click opens viewer without navigation; zoom, rotate, download and close work", async () => {
  const h=harness(response(imageBlob));
  const link=mockLink("https://example.org/api/task/1/attachments/2", {explicit:true, dataset:{imageName:"示例.png"}});
  let prevented=0, stopped=0;
  h.handlers.click.handler({button:0,defaultPrevented:false,target:{closest:()=>link},preventDefault(){prevented++;},stopPropagation(){stopped++;}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.handlers.click.capture,true);
  assert.equal(prevented,1); assert.equal(stopped,1);
  assert.equal(h.dialog.open,true);
  assert.equal(h.picture.hidden,false);
  assert.equal(h.name.textContent,"示例.png");
  assert.equal(h.fetchCalls[0].options.credentials,"same-origin");
  assert.equal(h.picture.src.startsWith("blob:"),true);
  h.dialogEvents.click({target:{closest:()=>({dataset:{ivAction:"in"}})},preventDefault(){}});
  assert.equal(h.zoom.textContent,"125%");
  h.dialogEvents.click({target:{closest:()=>({dataset:{ivAction:"rotate"}})},preventDefault(){}});
  assert.match(h.picture.style.transform,/rotate\(90deg\)/);
  h.dialogEvents.click({target:{closest:()=>({dataset:{ivAction:"download"}})},preventDefault(){}});
  assert.equal(h.anchors[0].downloadStarted,true);
  assert.equal(h.anchors[0].download,"示例.png");
  assert.equal(h.anchors[0].href,h.picture.src);
  assert.equal(h.status.textContent,"已发起下载");
  h.api.close();
  assert.equal(h.dialog.open,false);
  assert.equal(h.revoked.length,1);
});

test("rejects unrecognized download bytes rather than rendering HTML or PDF", async () => {
  const h=harness(response(new Blob(["<html>login</html>"])));
  await h.api.open("/api/task/2/attachments/3", "fake.png");
  assert.equal(h.dialog.open,true);
  assert.match(h.status.textContent,/不是支持的图片格式/);
  assert.equal(h.picture.hidden,true);
});

test("non-image attachments and modified clicks are never intercepted", () => {
  const h=harness(response(imageBlob));
  let count=0;
  const link=mockLink("https://example.org/report.zip");
  for (const attrs of [{},{ctrlKey:true}]) {
    h.handlers.click.handler({button:0,defaultPrevented:false,target:{closest:()=>attrs.ctrlKey?mockLink("https://example.org/a.png"):link},preventDefault(){count++;},stopPropagation(){},...attrs});
  }
  assert.equal(count,0);
});


test("clipboard copy converts raster input to PNG and reports success", async () => {
  const h=harness(response(imageBlob));
  let captured;
  h.ctx.ClipboardItem = function(value) { captured = value; return value; };
  h.ctx.navigator.clipboard = {write:async (items) => {assert.equal(items.length,1);}};
  await h.api.open("/shot.png", "shot.png");
  h.dialogEvents.click({target:{closest:()=>({dataset:{ivAction:"copy"}})},preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(captured["image/png"] instanceof Blob);
  assert.equal(h.status.textContent,"已复制图片到剪贴板");
});

test("viewer refuses off-origin programmatic image opens", async () => {
  const h=harness(response(imageBlob));
  await assert.rejects(h.api.open("https://outside.example/foo.png"),/只能在站内查看图片/);
  assert.equal(h.dialog.open,false);
});

test("Escape closes only the image viewer and preserves the underlying task form", async () => {
  const h=harness(response(imageBlob));
  let hidden=false, cleared=false;
  const taskModal={replaceChildren(){cleared=true;},className:"modal"};
  const mask={classList:{contains(){return hidden;},add(){hidden=true;}}};
  h.ctx.document.getElementById=id=>id === "modal-mask" ? mask : taskModal;
  h.ctx.document.body.classList={remove(){}};
  vm.runInContext(fs.readFileSync(path.join(__dirname,"../../static/ui-dialog.js"),"utf8"),h.ctx);
  let focused=0;
  await h.api.open("/shot.png","shot.png",{isConnected:true,focus(){focused++;}});
  const event={key:"Escape",target:{matches(){return false;}},defaultPrevented:false,cancelBubble:false,
    preventDefault(){this.defaultPrevented=true;},stopPropagation(){this.cancelBubble=true;}};
  h.dialogEvents.keydown(event);
  if(!event.cancelBubble)h.ctx.modalKeydown(event);
  assert.equal(hidden,false,"task dialog must stay open");
  assert.equal(cleared,false,"unsaved task input must survive image dismissal");
  assert.equal(h.dialog.open,false);
  assert.equal(event.defaultPrevented,true);
  assert.equal(h.revoked.length,1);
  assert.equal(focused,1);
});

test("viewer keys stay isolated from page shortcuts and task focus traps", async () => {
  const h=harness(response(imageBlob));
  await h.api.open("/shot.png");
  for(const key of ["Tab","r","+","c"]){
    const event={key,ctrlKey:key === "c",target:{matches(){return false;}},cancelBubble:false,
      preventDefault(){},stopPropagation(){this.cancelBubble=true;}};
    h.dialogEvents.keydown(event);
    assert.equal(event.cancelBubble,true,key+" must not reach underlying page handlers");
  }
});

test("native dialog cancellation closes the viewer and releases its image", async () => {
  const h=harness(response(imageBlob));
  await h.api.open("/shot.png");
  let prevented=false,stopped=false;
  assert.equal(typeof h.dialogEvents.cancel,"function");
  h.dialogEvents.cancel({preventDefault(){prevented=true;},stopPropagation(){stopped=true;}});
  assert.equal(prevented,true);assert.equal(stopped,true);
  assert.equal(h.dialog.open,false);assert.equal(h.revoked.length,1);
});

test("failed direct-image previews can download the original without interception", async () => {
  const h=harness(response(new Blob(["not a supported image"])));
  await h.api.open("/content/broken.png","broken.png");
  assert.match(h.status.textContent,/不是支持的图片格式/);
  h.dialogEvents.click({target:{closest:()=>({dataset:{ivAction:"download"}})},preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.anchors.length,1);
  assert.equal(h.anchors[0].href,"https://example.org/content/broken.png");
  assert.equal(h.anchors[0].download,"broken.png");
  assert.equal(h.anchors[0].downloadStarted,true);
  assert.equal(h.fetchCalls.length,1,"download must not open another preview request");
  assert.equal(h.status.textContent,"已发起下载");
});


const press = (h, operation) => h.dialogEvents.click({
  target:{closest:()=>({dataset:{ivAction:operation}})}, preventDefault(){}
});

test("fit uses natural pixel dimensions; 1:1 is distinct and rotation survives fitting", async () => {
  const h=harness(response(imageBlob));
  h.picture.naturalWidth=4000;
  h.picture.naturalHeight=3000;
  await h.api.open("/large.png","large.png");
  assert.equal(h.zoom.textContent,"19%");
  assert.equal(h.dimensions.textContent,"4000 × 3000");
  assert.equal(h.status.hidden,true,"no permanent help/status row on successful load");
  press(h,"rotate");
  assert.equal(h.zoom.textContent,"14%");
  assert.ok(h.picture.style.transform.includes("rotate(90deg)"));
  press(h,"actual");
  assert.equal(h.zoom.textContent,"100%");
  press(h,"fit");
  assert.equal(h.zoom.textContent,"14%");
  assert.ok(h.picture.style.transform.includes("rotate(90deg)"),"fit must preserve rotation");
});

test("fit recalculates when viewport changes, while manual zoom remains manual", async () => {
  const h=harness(response(imageBlob));
  h.picture.naturalWidth=4000; h.picture.naturalHeight=3000;
  await h.api.open("/large.png");
  assert.equal(h.zoom.textContent,"19%");
  h.stage.clientWidth=400;
  h.stage.clientHeight=600;
  // The fit action uses the current stage geometry, even without a ResizeObserver.
  press(h,"fit");
  assert.equal(h.zoom.textContent,"9%");
  press(h,"actual");
  assert.equal(h.zoom.textContent,"100%");
});

test("wheel zoom follows pointer and two touch pointers support pinch zoom", async () => {
  const h=harness(response(imageBlob));
  h.picture.naturalWidth=4000;h.picture.naturalHeight=3000;
  await h.api.open("/large.png");
  let blocked=false;
  h.stageEvents.wheel({deltaY:-1,clientX:500,clientY:200,preventDefault(){blocked=true;}});
  assert.equal(blocked,true);
  assert.ok(h.picture.style.transform.includes("translate(calc(-50% + -"),"pointer zoom shifts image around cursor");
  const before=parseInt(h.zoom.textContent,10);
  h.stageEvents.pointerdown({pointerId:1,pointerType:"touch",clientX:300,clientY:300});
  h.stageEvents.pointerdown({pointerId:2,pointerType:"touch",clientX:500,clientY:300});
  h.stageEvents.pointermove({pointerId:2,pointerType:"touch",clientX:550,clientY:300});
  assert.ok(parseInt(h.zoom.textContent,10)>before);
  h.stageEvents.pointerup({pointerId:2});
  h.stageEvents.pointerup({pointerId:1});
});
