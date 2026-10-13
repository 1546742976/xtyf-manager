
var App={currentPage:1,totalPages:1,selected:{},stats:null,searchTimer:null,_dupCache:null};
function showToast(msg,type){var t=document.getElementById('toast');t.textContent=msg;t.className='toast show '+(type||'info');clearTimeout(App._toastTimer);App._toastTimer=setTimeout(function(){t.classList.remove('show')},3500)}

async function loadStats(){
let s;
try {
  const r=await fetch('/invoices/api/stats');
  if(!r.ok) throw new Error('HTTP '+r.status);
  s=await r.json(); App.stats=s;
} catch(e) {
  showToast('发票统计加载失败：'+e.message,'error');
  return;
}
var ss=s.status_stats||{};
document.getElementById('statsGrid').innerHTML=[
{l:'发票总数',v:s.total_count,cls:'accent-blue'},
{l:'总金额',v:'\xA5'+Money.format(s.total_amount),cls:'accent-blue'},
{l:'已报销',v:(ss['已报销']||{}).count+' / \xA5'+Money.format((ss['已报销']||{}).amount),cls:'accent-green'},
{l:'正在报销',v:(ss['正在报销']||{}).count+' / \xA5'+Money.format((ss['正在报销']||{}).amount),cls:'accent-orange'},
{l:'未报销',v:(ss['未报销']||{}).count+' / \xA5'+Money.format((ss['未报销']||{}).amount),cls:'accent-red'},
{l:'提交人',v:Object.keys(s.submitter_stats||{}).filter(function(k){return k!=='未指定'}).length+' 人',cls:'accent-purple'},
].map(function(c){return '<div class="stat-card '+c.cls+'"><div class="label">'+c.l+'</div><div class="value">'+c.v+'</div></div>'}).join('');
['filterMonth','filterCategory'].forEach(function(id){setOptions(id,Object.keys(s[id.replace('filter','').toLowerCase()+'_stats']||{}).sort().reverse())});
try { await loadInvoiceOwners(); } catch (error) { showToast(error.message, 'error'); }
}
function setOptions(id,arr){var sel=document.getElementById(id);if(!sel)return;var cur=sel.value,first=sel.options[0];sel.innerHTML='';sel.appendChild(first);arr.forEach(function(v){var o=document.createElement('option');o.value=v;o.textContent=v;if(v===cur)o.selected=true;sel.appendChild(o)})}

let invoiceRequestSerial=0;
async function loadInvoices(page,highlightDups){
page=page||1;App.currentPage=page;
const serial=++invoiceRequestSerial;
var p=new URLSearchParams();
['filterStatus','filterMonth','filterCategory'].forEach(function(id){var el=document.getElementById(id);if(el&&el.value)p.set(id.replace('filter','').toLowerCase(),el.value)});
var ownerFilter=document.getElementById('filterSubmitter').value;if(ownerFilter)p.set('submitter_id',ownerFilter);
if(document.getElementById('filterOwnership')?.checked)p.set('ownership','unresolved');
var df=document.getElementById('filterDateFrom'),dt=document.getElementById('filterDateTo');
if(df.value)p.set('date_from',df.value);
if(dt.value)p.set('date_to',dt.value);
var kw=document.getElementById('searchInput').value.trim();
if(kw)p.set('search',kw);
p.set('page',page);p.set('per_page','50');
let data;
try {
  var r=await fetch('/invoices/api/invoices?'+p.toString());
  if (!r.ok) throw new Error('HTTP '+r.status);
  data=await r.json();
  if (!data || !Array.isArray(data.data)) throw new Error('数据格式错误');
} catch(e) {
  if(serial===invoiceRequestSerial) showToast('发票列表加载失败：'+e.message,'error');
  return;
}
if(serial!==invoiceRequestSerial) return;
App.totalPages=Math.ceil(data.total/data.per_page)||1;
var tb=document.getElementById('invoiceTableBody'),em=document.getElementById('emptyMsg');

var dupPaths=highlightDups?Object.values(App._dupCache||{}).flat():[];

if(data.data.length===0){tb.innerHTML='';em.style.display='block'}else{em.style.display='none';
tb.innerHTML=data.data.map(function(inv){
var sc=App.selected[inv.id]?' row-selected':'';
if(dupPaths.length&&dupPaths.indexOf(inv.rel_path)>=0)sc+=' row-dup';
var ah=Money.compare(inv.amount,'0.00')>0?'\xA5'+Money.format(inv.amount):'<span class="amount-col missing">\xA50.00</span>';
var st=inv.source==='人工确认'?'<span class="badge tag-filename">人工确认</span>':inv.source==='PDF'?'<span class="badge tag-pdf">PDF</span>':inv.source==='PDF(\u7f13\u5b58)'?'<span class="badge tag-pdf">PDF\u7f13\u5b58</span>':inv.source==='文件名'?'<span class="badge tag-filename">文件名</span>':'<span class="badge tag-unknown">待识别</span>';
var eid = escHtml(inv.id);
var erp = escHtml(inv.rel_path||'');
var subHtml=inv.submitter?'<span class="clickable" data-invoice-detail="'+eid+'">'+escHtml(inv.submitter)+'</span>':'<span style="color:#ccc;cursor:pointer" data-invoice-detail="'+eid+'">-</span>';
if(inv.ownership_unresolved)subHtml+=' <small>待确认</small>';else subHtml+=' <small>#'+Number(inv.submitter_id)+'</small>';
var invNum=inv.invoice_number?('<span class="clickable" title="'+escHtml(inv.invoice_number)+'">'+escHtml(inv.invoice_number.substring(inv.invoice_number.length-8))+'</span>'):'-';
var scol = inv.status==='已报销'?'#e6f4ea':inv.status==='正在报销'?'#fef7e0':'#fce8e6';
var unsel = inv.status==='未报销'?' selected':'';
var insel = inv.status==='正在报销'?' selected':'';
var resel = inv.status==='已报销'?' selected':'';
return '<tr class="'+sc+'" data-id="'+escHtml(inv.id)+'">'+
'<td class="col-check"><input type="checkbox" '+(App.selected[inv.id]?'checked':'')+' data-invoice-select="'+eid+'"></td>'+
'<td><span class="month-label">'+escHtml(inv.month)+'</span></td>'+
'<td>'+escHtml(inv.date||'-')+'</td>'+
'<td>'+(IS_FINANCE?'<select class="inline-select" data-invoice-status="'+eid+'" style="background:'+scol+'"><option value="\u672a\u62a5\u9500"'+unsel+'>\u672a\u62a5\u9500</option><option value="\u6b63\u5728\u62a5\u9500"'+insel+'>\u6b63\u5728\u62a5\u9500</option><option value="\u5df2\u62a5\u9500"'+resel+'>\u5df2\u62a5\u9500</option></select>':'<span class="badge" style="background:'+scol+'">'+inv.status+'</span>')+'</td>'+
'<td>'+subHtml+'</td>'+
'<td>'+escHtml(inv.category||'-')+'</td>'+
'<td><span class="clickable" data-invoice-file="'+erp+'">'+escHtml(inv.vendor)+'</span></td>'+
'<td class="amount-col">'+ah+'</td>'+
'<td style="font-size:10px">'+invNum+'</td>'+
'<td><button class="btn-sm" data-invoice-file="'+erp+'">\u6253\u5f00</button> <button class="btn-sm" data-invoice-detail="'+eid+'">'+(IS_FINANCE?'\u7f16\u8f91':'\u67e5\u770b')+'</button></td></tr>';
}).join('');document.getElementById('selectAll').checked=false}
renderPagination(data.total,page);updateBatchBar()
}
function bindInvoiceTable(){
var table=document.getElementById('invoiceTableBody');
table.addEventListener('click',function(event){
  var file=event.target.closest('[data-invoice-file]');
  var detail=event.target.closest('[data-invoice-detail]');
  if(file)openFile(file.dataset.invoiceFile);
  else if(detail)DetailModal.open(detail.dataset.invoiceDetail);
});
table.addEventListener('change',function(event){
  var select=event.target.closest('[data-invoice-select]');
  var status=event.target.closest('[data-invoice-status]');
  if(select)toggleOne(select.dataset.invoiceSelect,select.checked);
  else if(status)quickSetStatus(status.dataset.invoiceStatus,status.value);
});
}
function applyFilters(){loadInvoices(1);loadStats()}
function debounceSearch(){clearTimeout(App.searchTimer);App.searchTimer=setTimeout(function(){loadInvoices(1)},400)}
function renderPagination(total,page){var pg=document.getElementById('pagination'),tp=App.totalPages;if(tp<=1){pg.innerHTML='';return}var h='<span class="info">共 '+total+' 条 / '+tp+' 页</span>';h+='<button '+(page<=1?'disabled':'')+' onclick="loadInvoices('+(page-1)+')">上一页</button>';for(var i=1;i<=tp;i++){if(i===1||i===tp||(i>=page-2&&i<=page+2))h+='<button class="'+(i===page?'active':'')+'" onclick="loadInvoices('+i+')">'+i+'</button>';else if(i===page-3||i===page+3)h+='<span>...</span>'}h+='<button '+(page>=tp?'disabled':'')+' onclick="loadInvoices('+tp+')">末页</button>';pg.innerHTML=h}

function toggleOne(id,checked){if(checked)App.selected[id]=true;else delete App.selected[id];updateBatchBar()}
function toggleSelectAll(){var c=document.getElementById('selectAll').checked,cbs=document.querySelectorAll('#invoiceTableBody input[type=checkbox]');cbs.forEach(function(cb){var tr=cb.closest('tr');if(!tr)return;var id=tr.getAttribute('data-id');if(!id)return;if(c)App.selected[id]=true;else delete App.selected[id];cb.checked=c});updateBatchBar()}
function updateBatchBar(){var b=document.getElementById('batchBar'),n=Object.keys(App.selected).length;if(n>0){b.classList.add('show');document.getElementById('batchCount').textContent='已选 '+n+' 张'}else{b.classList.remove('show')}}
function clearSelection(){App.selected={};updateBatchBar();loadInvoices(App.currentPage)}
function escHtml(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function jsEscape(s){return s.replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/\n/g,'\\n').replace(/\r/g,'')}

async function doBatchUpdate(){
var ids=Object.keys(App.selected);if(!ids.length)return;
var u={},st=document.getElementById('batchStatus'),su=document.getElementById('batchSubmitter');
if(st.value)u.status=st.value;
if(su.value)u.submitter_id=Number(su.value);
if(!Object.keys(u).length){showToast('请选择要修改的字段','error');return}
var r=await fetch('/invoices/api/invoice/batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:ids,updates:u})});
var d=await r.json();
if(d.success){showToast('已更新 '+d.count+' 张'+(d.moved?'，已移动 '+d.moved+' 个文件':''),'success');clearSelection();App.stats=null;loadStats();loadInvoices(App.currentPage)}else{showToast(d.error||'批量更新失败','error')}}

async function quickSetStatus(id,status){
var r=await fetch('/invoices/api/invoice/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,status:status})});
var d=await r.json();
if(d.success){App.stats=null;loadStats();loadInvoices(App.currentPage);showToast(d.moved?'状态已更新，文件已移动':'状态已更新','success')}else{showToast(d.error||'状态更新失败','error');loadInvoices(App.currentPage)}}

// ====== 进度条 ======
var Progress={show:function(msg){document.getElementById('progressText').textContent=msg||'处理中...';document.getElementById('progressFill').style.width='0%';document.getElementById('progressPercent').textContent='0%';document.getElementById('progressBar').style.display='block'},update:function(pct,msg){document.getElementById('progressFill').style.width=(pct||0)+'%';document.getElementById('progressPercent').textContent=Math.round(pct||0)+'%';if(msg)document.getElementById('progressText').textContent=msg},hide:function(){document.getElementById('progressBar').style.display='none'}};

// ====== XMLHttpRequest 工具 ======
function xhrUpload(url,formData,onProgress){
return new Promise(function(resolve,reject){
var xhr=new XMLHttpRequest();
xhr.open('POST',url);
xhr.setRequestHeader('X-CSRF-Token',document.querySelector('meta[name="csrf-token"]').content);
xhr.upload.onprogress=function(e){if(e.lengthComputable&&onProgress)onProgress(Math.round(e.loaded/e.total*100))};
xhr.onload=function(){try{resolve(JSON.parse(xhr.responseText))}catch(e){reject(new Error('解析响应失败'))}};
xhr.onerror=function(){reject(new Error('网络错误'))};
xhr.ontimeout=function(){reject(new Error('请求超时'))};
xhr.timeout=180000;
xhr.send(formData);
});}

// ====== 拖入 ======
var dropZone=document.getElementById('dropZone');
dropZone.addEventListener('dragover',function(e){e.preventDefault();dropZone.classList.add('drag-over')});
dropZone.addEventListener('dragleave',function(){dropZone.classList.remove('drag-over')});
dropZone.addEventListener('drop',function(e){e.preventDefault();dropZone.classList.remove('drag-over');handleFiles(e.dataTransfer.files)});
// Replace the template's legacy onchange handler instead of uploading twice.
document.getElementById('fileInput').onchange=function(){handleFiles(this.files);this.value=''};

async function handleFiles(files){
if(!files||!files.length)return;
var pdfs=[];for(var i=0;i<files.length;i++){if(files[i].name.toLowerCase().endsWith('.pdf'))pdfs.push(files[i])}
if(!pdfs.length){showToast('请选择PDF文件','error');return}

if(pdfs.length===1){
var fd=new FormData();fd.append('file',pdfs[0]);
Progress.show('上传并解析: '+pdfs[0].name);
try{
var d=await xhrUpload('/invoices/api/upload',fd,function(pct){Progress.update(pct,'上传中 '+pct+'%')});
Progress.update(90,'AI解析中...');
Progress.hide();
if(d.success){quickConfirm(d)}else{showToast(d.error,'error')}
}catch(e){Progress.hide();showToast('上传失败: '+e.message,'error')}
}else{
Progress.show('准备上传 '+pdfs.length+' 个PDF');
var fd2=new FormData();
for(var j=0;j<pdfs.length;j++)fd2.append('files',pdfs[j]);
try{
var d2=await xhrUpload('/invoices/api/upload/batch',fd2,function(pct){
Progress.update(Math.min(pct*0.6,60),'上传中 '+pct+'% ('+pdfs.length+'个文件)');
});
Progress.update(80,'AI解析发票信息...');
setTimeout(function(){Progress.update(90,'完成解析')},200);
Progress.hide();
if(d2.success){BUModal.open(d2.results)}else{showToast('上传失败: '+(d2.error||'未知错误'),'error')}
}catch(e){Progress.hide();showToast('上传失败: '+e.message,'error')}
}
}

function cleanupTmp(paths){if(!paths||!paths.length)return;fetch('/invoices/api/upload/cleanup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tmp_paths:paths})})}

async function quickConfirm(d){
if(IS_FINANCE){await BUModal.open([d]);return}
var sub=ME_NAME;
if(!sub||!sub.trim()){showToast('需要填写提交人才能入库','error');cleanupTmp([d.tmp_path]);return}
var body={tmp_path:d.tmp_path,original_name:d.original_name,month:d.info.month||'未归类',status:'未报销',submitter:sub.trim(),category:d.info.category||'',notes:'',amount:d.info.amount||'0.00',vendor:d.info.vendor||'',date:d.info.date||'',invoice_number:d.info.invoice_number||'',skip_duplicate_check:!d.has_duplicate||confirm('检测到可能重复，仍要入库吗？')};
Progress.show('正在入库...');
try{var r=await fetch('/invoices/api/upload/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});var dd=await r.json();Progress.hide();if(dd.success){showToast(dd.message,'success');App.stats=null;loadStats();loadInvoices(1)}else if(dd.code==='DUPLICATE'){showToast('存在重复发票，已拦截','error');cleanupTmp([d.tmp_path])}else{showToast(dd.error,'error');cleanupTmp([d.tmp_path])}}
catch(e){Progress.hide();showToast('入库失败: '+e.message,'error')}
}

// ====== 批量上传弹窗 ======
var BUModal={
_data:[],
open:async function(results){
try{await loadInvoiceOwners()}catch(error){showToast(error.message,'error');return}
BUModal._data=results;
document.getElementById('buTotal').textContent=results.length;
document.getElementById('buSubmitter').value='';
var tbody=document.getElementById('buTbody');
tbody.innerHTML=results.map(function(r,idx){
if(!r.success)return '<tr><td>'+(idx+1)+'</td><td colspan="9" style="color:#d93025">'+escHtml(r.original_name)+': '+escHtml(r.error)+'</td></tr>';
var d=r.info||{};
var dupClass=r.has_duplicate?' dup-row':'';
var dupInfo=r.has_duplicate?'<span class="badge tag-dup" title="'+r.duplicates.map(function(dd){return escHtml(dd.path)}).join('\n')+'">重复!</span>':'-';
return '<tr class="'+dupClass+'">'+
'<td>'+(idx+1)+'</td>'+
'<td title="'+escHtml(r.original_name)+'">'+escHtml(r.original_name.substring(0,25))+'</td>'+
'<td><input type="number" step="any" min="0" title="金额按分四舍五入" value="'+(d.amount||'')+'" style="width:70px" class="bu-amount-'+idx+'"></td>'+
'<td><input type="text" value="'+(d.date||'')+'" style="width:90px" class="bu-date-'+idx+'"></td>'+
'<td><input type="text" value="'+(d.month||'')+'" style="width:55px" class="bu-month-'+idx+'"></td>'+
'<td><input type="text" value="'+escHtml(d.vendor||'')+'" style="width:120px" class="bu-vendor-'+idx+'"></td>'+
'<td><input type="text" value="'+(d.invoice_number||'')+'" style="width:100px" class="bu-inum-'+idx+'"></td>'+
'<td><input type="text" value="'+(d.category||'')+'" style="width:60px" class="bu-cat-'+idx+'"></td>'+
'<td><select aria-label="提交人" style="min-width:100px" class="bu-sub-'+idx+'"'+(IS_FINANCE?'':' disabled')+'>'+invoiceOwnerOptions(IS_FINANCE?'':ME_ID,'使用统一提交人')+'</select></td>'+
'<td>'+dupInfo+'</td></tr>';
}).join('');
document.getElementById('batchUploadModal').classList.add('show');
},
close:function(){
document.getElementById('batchUploadModal').classList.remove('show');
var paths=BUModal._data.filter(function(r){return r.success}).map(function(r){return r.tmp_path});
BUModal._data=[];
cleanupTmp(paths);
},
confirm:async function(){
var dupN=BUModal._data.filter(function(r){return r.success&&r.has_duplicate}).length;
if(dupN>0&&!confirm('有 '+dupN+' 张疑似重复发票, 仍要入库吗?'))return;
var globalSub=IS_FINANCE?document.getElementById('buSubmitter').value:String(ME_ID);
var globalStatus=IS_FINANCE?document.getElementById('buStatus').value:'未报销';
var items=[];
for(var i=0;i<BUModal._data.length;i++){
var r=BUModal._data[i];
if(!r.success)continue;
var sub=document.querySelector('.bu-sub-'+i);
items.push({tmp_path:r.tmp_path,original_name:r.original_name,status:globalStatus,submitter_id:Number((sub&&sub.value)||globalSub),category:document.querySelector('.bu-cat-'+i).value,notes:'',amount:document.querySelector('.bu-amount-'+i).value||'0.00',vendor:document.querySelector('.bu-vendor-'+i).value,date:document.querySelector('.bu-date-'+i).value,month:document.querySelector('.bu-month-'+i).value||'未归类',invoice_number:document.querySelector('.bu-inum-'+i).value});
}
if(!items.every(function(it){return it.submitter_id>0})){showToast('请选择每张发票的提交人或统一提交人','error');return}
Progress.show('正在入库 '+items.length+' 张发票...');
try{
var r2=await fetch('/invoices/api/upload/confirm/batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items:items})});
var d2=await r2.json();
Progress.hide();
if(d2.success){
var failed=new Set((d2.results||[]).filter(function(item){return item.status==='error'}).map(function(item){return item.tmp_path}));
if(failed.size){var remaining=BUModal._data.filter(function(item){return failed.has(item.tmp_path)});await BUModal.open(remaining);showToast(d2.message+'；失败条目已保留，请修改后重试','error')}else{BUModal.close();showToast(d2.message,'success')}
App.stats=null;loadStats();loadInvoices(1)}
else{showToast('入库失败','error')}
}catch(e){Progress.hide();showToast('入库失败: '+e.message,'error')}
}
};
document.getElementById('batchUploadModal').addEventListener('click',function(e){if(e.target===this)BUModal.close()});

// ====== 持久化任务列表与可重连 SSE ======
var InvoiceJobs={
  names:{scan_dups:'查重',organize:'整理文件',refresh:'刷新 PDF 金额',delete_dups:'删除重复'},
  states:{queued:'排队中',running:'执行中',done:'已完成',error:'失败',interrupted:'执行中断'},
  active:new Map(),timer:null,loading:false,
  resetButtons:function(){
    var organize=document.getElementById('organizeBtn'),refresh=document.getElementById('refreshBtn');
    if(organize){organize.disabled=false;organize.textContent='整理文件'}
    if(refresh){refresh.disabled=false;refresh.textContent='刷新PDF金额'}
  },
  load:async function(){
    if(!IS_FINANCE||InvoiceJobs.loading)return;
    clearTimeout(InvoiceJobs.timer);InvoiceJobs.loading=true;
    var delay=15000;
    try{
      var response=await fetch('/invoices/api/jobs');
      if(response.status===401||response.status===403){
        InvoiceJobs.active.forEach(function(entry){entry.source.close()});InvoiceJobs.active.clear();
        document.getElementById('invoiceJobsList').textContent='请重新登录并确认财务权限';
        return;
      }
      if(!response.ok)throw new Error('任务列表加载失败');
      var data=await response.json(),jobs=data.jobs||[];
      if(jobs.some(function(job){return job.status==='queued'||job.status==='running'}))delay=3000;
      document.getElementById('invoiceJobsList').innerHTML=jobs.length?jobs.map(function(job){
        var date=new Date(job.created_at*1000).toLocaleString();
        return '<div class="invoice-job-row" data-status="'+escHtml(job.status)+'"><div class="invoice-job-info"><strong>'+escHtml(InvoiceJobs.names[job.type]||job.type)+'</strong> · '+escHtml(InvoiceJobs.states[job.status]||job.status)+' · '+Number(job.progress)+'%<small>'+escHtml(job.message)+'</small><small>'+escHtml(job.actor_name)+' · '+escHtml(date)+'</small></div><button type="button" class="btn-sm" data-job-id="'+escHtml(job.id)+'">查看'+(['queued','running'].includes(job.status)?'进度':'结果')+'</button></div>';
      }).join(''):'暂无后台任务';
      document.querySelectorAll('#invoiceJobsList [data-job-id]').forEach(function(button){button.addEventListener('click',function(){InvoiceJobs.view(button.dataset.jobId)})});
      for(var job of jobs){
        if(InvoiceJobs.active.has(job.id)&&['done','error','interrupted'].includes(job.status)){
          var detail=await fetch('/invoices/api/job/'+encodeURIComponent(job.id));
          if(detail.ok)InvoiceJobs.finish(job.id,await detail.json());
        }
      }
    }catch(error){document.getElementById('invoiceJobsList').textContent=error.message+'，稍后自动重试';delay=5000}
    finally{InvoiceJobs.loading=false}
    InvoiceJobs.timer=setTimeout(InvoiceJobs.load,delay);
  },
  watch:function(id,onResult){
    if(InvoiceJobs.active.has(id))return;
    var source=new EventSource('/invoices/api/job/stream/'+encodeURIComponent(id));
    InvoiceJobs.active.set(id,{source:source,onResult:onResult});
    source.onmessage=function(event){
      try{
        var data=JSON.parse(event.data);
        if(['done','error','interrupted'].includes(data.status))InvoiceJobs.finish(id,data);
      }catch(error){showToast('任务进度解析失败，可在任务列表查看结果','error')}
    };
    // EventSource reconnects automatically. The list also retrieves terminal results.
    source.onerror=function(){InvoiceJobs.load()};
    InvoiceJobs.load();
  },
  finish:function(id,data){
    var entry=InvoiceJobs.active.get(id);if(!entry)return;
    entry.source.close();InvoiceJobs.active.delete(id);InvoiceJobs.resetButtons();
    if(data.status==='done'){
      var result=data.result||{};
      if(result.failed||result.errors){
        App.stats=null;loadStats();loadInvoices(App.currentPage);
        InvoiceJobs.showResult(data);showToast('任务完成，部分项目失败，请查看结果','error');
      }else if(entry.onResult)entry.onResult(data);
      else{App.stats=null;loadStats();loadInvoices(App.currentPage);InvoiceJobs.showResult(data)}
    }else{showToast(data.message||'任务执行失败','error');InvoiceJobs.showResult(data)}
    InvoiceJobs.load();
  },
  view:async function(id){
    try{
      var response=await fetch('/invoices/api/job/'+encodeURIComponent(id));
      var job=await response.json();if(!response.ok)throw new Error(job.error||'任务读取失败');
      if(job.status==='queued'||job.status==='running'){
        InvoiceJobs.watch(id);showToast(job.message+' · '+job.progress+'%','info');
      }else if(job.type==='scan_dups'&&job.status==='done')showDuplicateResult(job);
      else InvoiceJobs.showResult(job);
    }catch(error){showToast(error.message,'error')}
  },
  showResult:function(job){
    var result=job.result||{},lines=[];
    var labels={moved:'移动文件',skipped:'跳过文件',errors:'失败项目',empty_dirs_removed:'清理空目录',total:'发票总数',pdf_extracted:'识别金额',deleted:'删除文件',failed:'失败文件',groups:'重复组',dup_count:'重复项目'};
    Object.keys(labels).forEach(function(key){if(result[key]!==undefined)lines.push(labels[key]+'：'+result[key])});
    (result.details||[]).forEach(function(item){lines.push(String(item))});
    (result.failed_details||[]).forEach(function(item){lines.push(item.path+'：'+item.error)});
    if(result.deleted_paths&&result.deleted_paths.length)lines.push('已删除文件（最多显示 50 项）：',...result.deleted_paths);
    document.getElementById('invoiceJobResultTitle').textContent=(InvoiceJobs.names[job.type]||'后台任务')+' · '+(InvoiceJobs.states[job.status]||'结果');
    document.getElementById('invoiceJobResultMessage').textContent=job.message||result.error||'';
    document.getElementById('invoiceJobResultDetails').textContent=lines.join('\n');
    document.getElementById('invoiceJobModal').classList.add('show');
  }
};
async function startJob(type,onResult){
  try{
    var response=await fetch('/invoices/api/job/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:type})});
    var data=await response.json();if(!response.ok||!data.job_id)throw new Error(data.error||'启动失败');
    InvoiceJobs.watch(data.job_id,onResult);
    showToast(data.created?'任务已提交，可在后台任务中查看':'已有同类任务，已连接到当前任务','info');
  }catch(error){showToast('启动失败：'+error.message,'error')}
  finally{InvoiceJobs.resetButtons()}
}

// ====== 查重 ======
function scanDuplicates(){startJob('scan_dups',showDuplicateResult)}
function showDuplicateResult(data){
var res=data.result||{};
var dups=res.duplicates||{};
var dupCount=res.dup_count||0;
var allPaths=res.all_dup_paths||[];
App._dupCache=allPaths.length?{_all:allPaths}:null;
if(dupCount===0){
showToast('未发现重复发票','success');
loadInvoices(App.currentPage,false);
}else{
var msg='发现 '+dupCount+' 个重复项（'+Object.keys(dups).length+' 组）';
if(allPaths.length>0)msg+='，'+allPaths.length+' 个文件已高亮';
showToast(msg,'error');
loadInvoices(App.currentPage,true);
// 显示删除确认弹窗
DelDupModal.show(dups, allPaths);
}
}

var DelDupModal={
_data:null,
show:function(dups, allPaths){
DelDupModal._data={dups:dups,allPaths:allPaths};
document.getElementById('delDupCount').textContent=Object.keys(dups).length+' 组';
var list=[];
Object.entries(dups).forEach(function(e){
var num=e[0];
var paths=e[1];
list.push('<div style="margin-bottom:6px;padding:4px 8px;background:#fff8e1;border-radius:4px"><b>发票号:</b> '+escHtml(num)+' ('+paths.length+'个文件)</div>');
paths.forEach(function(p,pi){
if(pi===0)list.push('<div style="margin-left:16px;color:#0d904f">  \u2714 保留: '+escHtml(p)+'</div>');
else list.push('<div style="margin-left:16px;color:#d93025">  \u2716 删除: '+escHtml(p)+'</div>');
});
});
document.getElementById('delDupList').innerHTML=list.join('');
document.getElementById('delDupModal').classList.add('show');
},
close:function(){
document.getElementById('delDupModal').classList.remove('show');
DelDupModal._data=null;
},
confirm:async function(){
if(!confirm('确定要删除重复文件吗？此操作不可恢复！'))return;
DelDupModal.close();
startJob('delete_dups', function(){
showToast('重复文件已删除','success');
App._dupCache=null;
App.stats=null;
loadStats();
loadInvoices(1,false);
});
}
};
document.getElementById('delDupModal').addEventListener('click',function(e){if(e.target===this)DelDupModal.close()});

// ====== 详情弹窗 (保持不变) ======
var DetailModal={
_id:'',
open:async function(id){DetailModal._id=id;var r=await fetch('/invoices/api/invoices?id='+encodeURIComponent(id)),d=await r.json(),inv=d.data.length?d.data[0]:null;if(!inv){showToast('未找到发票','error');return}
document.getElementById('detMonth').value=inv.month;
document.getElementById('detDate').value=inv.date||'';
document.getElementById('detStatus').value=inv.status||'未报销';
document.getElementById('detReimbursedDate').value=inv.reimbursed_date||'';
try{await loadInvoiceOwners()}catch(error){showToast(error.message,'error');return}
document.getElementById('detSubmitter').innerHTML=invoiceOwnerOptions(inv.submitter_id||0,inv.submitter?'待确认：'+inv.submitter:'待确认 / 未指定',true);
document.getElementById('detSubmitter').value=String(inv.submitter_id||0);
document.getElementById('detCategory').value=inv.category||'';
document.getElementById('detVendor').value=inv.vendor;
document.getElementById('detAmount').value='\xA5'+Money.format(inv.amount);
document.getElementById('detNotes').value=inv.notes||'';
document.getElementById('detInvNum').value=inv.invoice_number||'';
document.getElementById('detTags').value=(inv.tags||[]).join(', ');
document.getElementById('detFilename').value=inv.filename;
document.getElementById('detSource').value=inv.source||'未知';
document.getElementById('detPath').value=inv.rel_path||'';
document.getElementById('detailModal').classList.add('show')},
close:function(){document.getElementById('detailModal').classList.remove('show');DetailModal._id=''},
save:async function(){
var u={status:document.getElementById('detStatus').value,submitter_id:Number(document.getElementById('detSubmitter').value),notes:document.getElementById('detNotes').value.trim(),category_override:document.getElementById('detCategory').value.trim(),reimbursed_date:document.getElementById('detReimbursedDate').value,invoice_number:document.getElementById('detInvNum').value.trim(),tags:document.getElementById('detTags').value.split(',').map(function(s){return s.trim()}).filter(Boolean)};
var r=await fetch('/invoices/api/invoice/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({id:DetailModal._id},u))});
var d=await r.json();
if(d.success){showToast('保存成功'+(d.moved?'，文件已移动':''),'success');DetailModal.close();App.stats=null;loadStats();loadInvoices(App.currentPage)}else{showToast(d.error||'发票保存失败','error')}},
clearMeta:async function(){if(!confirm('确定要清除该发票的元数据？'))return;var r=await fetch('/invoices/api/invoice/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:DetailModal._id,submitter_id:0,notes:'',category_override:'',reimbursed_date:'',tags:[],invoice_number:''})});if((await r.json()).success){showToast('已清除','success');DetailModal.close();App.stats=null;loadStats();loadInvoices(App.currentPage)}}
};
document.getElementById('detailModal').addEventListener('click',function(e){if(e.target===this)DetailModal.close()});

// ====== 统计弹窗 (精简) ======
var StatsModal={show:async function(){
if(!App.stats){var r=await fetch('/invoices/api/stats');App.stats=await r.json()}var s=App.stats,ss=s.status_stats||{};
document.getElementById('modalStatsGrid').innerHTML=[{l:'发票总数',v:s.total_count,cls:'accent-blue'},{l:'总金额',v:'\xA5'+Money.format(s.total_amount),cls:'accent-blue'},{l:'已报销',v:(ss['已报销']||{}).count+'张 / \xA5'+Money.format((ss['已报销']||{}).amount),cls:'accent-green'},{l:'正在报销',v:(ss['正在报销']||{}).count+'张 / \xA5'+Money.format((ss['正在报销']||{}).amount),cls:'accent-orange'},{l:'未报销',v:(ss['未报销']||{}).count+'张 / \xA5'+Money.format((ss['未报销']||{}).amount),cls:'accent-red'}].map(function(o){return '<div class="stat-card '+o.cls+'"><div class="label">'+o.l+'</div><div class="value">'+o.v+'</div></div>'}).join('');
var months=Object.entries(s.month_stats||{}).sort(),maxM=Money.max(months.map(function(e){return e[1].amount}));document.getElementById('monthChart').innerHTML=months.map(function(e){return '<div class="chart-bar"><div class="name">'+escHtml(e[0])+'</div><div class="bar-wrap"><div class="bar-fill" style="width:'+Money.percent(e[1].amount,maxM)+'%"></div></div><div class="bar-value">\xA5'+Money.format(e[1].amount)+' ('+e[1].count+'张)</div></div>'}).join('');
document.getElementById('statusChart').innerHTML=['未报销','正在报销','已报销'].map(function(st){var d=ss[st]||{count:0,amount:'0.00'},cls=st==='已报销'?'accent-green':st==='正在报销'?'accent-orange':'accent-red',pct=s.total_count?(d.count/s.total_count*100).toFixed(1):0;return '<div class="chart-bar '+cls+'"><div class="name">'+st+'</div><div class="bar-wrap"><div class="bar-fill" style="width:'+Math.max(pct,2)+'%"></div></div><div class="bar-value">\xA5'+Money.format(d.amount)+' ('+d.count+'张)</div></div>'}).join('');
var cats=Object.entries(s.category_stats||{}).sort(function(a,b){return Money.compare(b[1].amount,a[1].amount)}),maxC=Money.max(cats.map(function(e){return e[1].amount}));document.getElementById('categoryChart').innerHTML=cats.map(function(e){return '<div class="chart-bar"><div class="name">'+escHtml(e[0])+'</div><div class="bar-wrap"><div class="bar-fill" style="width:'+Money.percent(e[1].amount,maxC)+'%"></div></div><div class="bar-value">\xA5'+Money.format(e[1].amount)+' ('+e[1].count+'张)</div></div>'}).join('');
var subs=Object.entries(s.submitter_stats||{}).sort(function(a,b){return Money.compare(b[1].amount,a[1].amount)}),maxS=Money.max(subs.map(function(e){return e[1].amount}));document.getElementById('submitterChart').innerHTML=subs.length===0?'<div style="color:#aaa;text-align:center;padding:20px">暂无提交人数据</div>':subs.map(function(e){return '<div class="chart-bar"><div class="name">'+escHtml(e[0])+'</div><div class="bar-wrap"><div class="bar-fill" style="width:'+Money.percent(e[1].amount,maxS)+'%"></div></div><div class="bar-value">\xA5'+Money.format(e[1].amount)+' ('+e[1].count+'张)</div></div>'}).join('');
document.getElementById('statsModal').classList.add('show')},close:function(){document.getElementById('statsModal').classList.remove('show')}};
document.getElementById('statsModal').addEventListener('click',function(e){if(e.target===this)StatsModal.close()});

// ====== 文件操作 ======
async function openFile(relPath){var r=await fetch('/invoices/api/open_file?rel_path='+encodeURIComponent(relPath)),d=await r.json();if(d.success&&d.url){var a=document.createElement('a');a.href=d.url;a.target='_blank';document.body.appendChild(a);a.click();a.remove()}else{showToast((d&&d.error)||'打开失败','error')}}
function openFolder(folder){showToast('服务端不提供文件夹浏览，请使用发票预览','info')}
function exportCSV(){
  const a=document.createElement('a');
  a.href='/invoices/api/export/download';
  a.download='invoices.csv';
  document.body.appendChild(a); a.click(); a.remove();
  showToast('已开始下载 CSV','success');
}
function exportZip(){showToast('正在打包全部发票，请稍候...','info');window.location.href='/invoices/api/export_zip'}

async function organizeFiles(){
var btn=document.getElementById('organizeBtn');
try{
var pv=await fetch('/invoices/api/organize/preview'),pd=await pv.json();
if(pd.count===0){showToast('没有需要整理的文件','info');return}
if(!confirm('整理预览: 共 '+pd.count+' 个文件将被归档到对应的 月份/状态 文件夹并标准化命名, 是否继续?'))return;
}catch(e){}
btn.disabled=true;btn.textContent='整理中...';
startJob('organize', function(){
btn.disabled=false;btn.textContent='整理文件';
App.stats=null;loadStats();loadInvoices(1);
showToast('整理完成','success');
});
}

async function refreshPDF(){
var b1=document.getElementById('refreshBtn');b1.disabled=true;b1.textContent='识别中...';
startJob('refresh', function(){
b1.disabled=false;b1.textContent='刷新PDF金额';
App.stats=null;loadStats();loadInvoices(App.currentPage);
showToast('PDF金额刷新完成','success');
});
}

window.IS_FINANCE=window.IS_FINANCE||false;window.ME_NAME=window.ME_NAME||'';
if(!window.IS_FINANCE){document.body.classList.add('member');document.querySelectorAll('#detailModal input,#detailModal select,#detailModal textarea').forEach(function(el){el.disabled=true});}
bindInvoiceTable();
loadStats().then(function(){loadInvoices()});
if(window.IS_FINANCE)InvoiceJobs.load();
window.addEventListener('beforeunload',function(){InvoiceJobs.active.forEach(function(entry){entry.source.close()})});

function invoiceOwnerOptions(selected, placeholder, allowUnassigned){
  var zero=allowUnassigned?'0':'';
  return '<option value="'+zero+'">'+escHtml(placeholder)+'</option>'+(App.owners||[]).map(function(member){
    return '<option value="'+Number(member.id)+'"'+(String(member.id)===String(selected)?' selected':'')+'>'+escHtml(member.name)+' · '+escHtml(member.username||'')+' (#'+Number(member.id)+')'+(!member.is_active||member.deleted_at?'（停用）':'')+'</option>';
  }).join('');
}
async function loadInvoiceOwners(){
  if(!App.owners){
    if(IS_FINANCE){
      var response=await fetch('/invoices/api/owners');
      var data=await response.json();
      if(!response.ok||!Array.isArray(data.members))throw new Error(data.error||'提交人加载失败');
      App.owners=data.members;
    }else App.owners=[{id:ME_ID,name:ME_NAME,username:'',is_active:1}];
  }
  [['filterSubmitter','全部提交人'],['batchSubmitter','-- 批量设提交人 --'],['buSubmitter','选择统一提交人']].forEach(function(pair){
    var select=document.getElementById(pair[0]),value=select.value;
    select.innerHTML=invoiceOwnerOptions(value,pair[1]);select.value=value;
  });
}
