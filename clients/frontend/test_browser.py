"""Browser integration against an isolated Flask database and simulated native IPC.

This verifies packaged UI behavior, not Android/WebView2 target-system execution.
Run after build_frontend.py with Python Playwright and its Chromium installed.
"""
import base64
import functools
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
import io
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[2]
TEMP=ROOT/'.build-tools/client-ui-tests/tmp'
TEMP.mkdir(parents=True,exist_ok=True)
tempfile.tempdir=str(TEMP)
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
print('Loading isolated Flask fixture...',flush=True)
from test_tasks import app_module  # sets all data/secret paths to fresh temp directories
from playwright.sync_api import sync_playwright


def run():
    print('Preparing browser session...',flush=True)
    app=app_module.app
    client=app.test_client()
    initial=client.post('/login',json={'username':'admin','password':'ci-test-password-only'})
    assert initial.status_code==200 and initial.get_json()['ok'], 'isolated fixture login failed'
    # SecurityAwareTestClient completes the real first-login password rotation.
    client.post('/logout')
    content=b'BT /F1 18 Tf 50 750 Td (Packaged PDF preview) Tj ET'
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream']
    pdf_buffer=io.BytesIO();pdf_buffer.write(b'%PDF-1.4\n');offsets=[]
    for index,obj in enumerate(objects,1):offsets.append(pdf_buffer.tell());pdf_buffer.write(str(index).encode()+b' 0 obj\n'+obj+b'\nendobj\n')
    xref=pdf_buffer.tell();pdf_buffer.write(b'xref\n0 6\n0000000000 65535 f \n')
    for offset in offsets:pdf_buffer.write(f'{offset:010} 00000 n \n'.encode())
    pdf_buffer.write(b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n');pdf_bytes=pdf_buffer.getvalue()
    state={'offline':False,'legacy':False,'calls':[],'serverUrl':'https://server.invalid','selected':[{'handle':'test-file-1','name':'sample.pdf','mime':'application/pdf','size':23}]}
    def native(action,args):
        state['calls'].append((action,args))
        if action=='get_config':return {'serverUrl':state['serverUrl'],'version':'test'}
        if action=='set_server':state['serverUrl']=args['serverUrl'];return {'serverUrl':state['serverUrl'],'version':'test'}
        if action=='cancel':return {}
        if action=='pick_files':return state['selected']
        if action=='download':return {'cancelled':False,'path':'test-download'}
        if action=='open_external':return {}
        if action in ('request','read_resource'):
            if state['offline']:raise RuntimeError('离线测试')
            if state['legacy'] and args['path']=='/api/client/bootstrap':
                status=200 if state['legacy'] in ('html200','array200','object200') else 503 if state['legacy']=='503' else 404
                body='[]' if state['legacy']=='array200' else '{}' if state['legacy']=='object200' else '<!doctype html><html><body>Old login page</body></html>'
                return {'status':status,'headers':{'Content-Type':'text/html'},'body':body,'encoding':'text'}
            if args['path']=='/__upload_test':return {'status':200,'headers':{'Content-Type':'application/json'},'body':json.dumps(args.get('multipart')),'encoding':'text'}
            if args['path']=='/__preview.pdf':return {'status':200,'headers':{'Content-Type':'application/pdf'},'body':base64.b64encode(pdf_bytes).decode(),'encoding':'base64'}
            headers={**args.get('headers',{}),'X-XTYF-Client-Protocol':'1'}
            response=client.open(args['path'],method=args.get('method','GET'),headers=headers,data=args.get('body'))
            if args['path']=='/funds/api/records' and args.get('method')=='POST' and state.get('drop_funds_response'):
                state['drop_funds_response']=False
                assert response.status_code==200
                return {'status':200,'headers':{'Content-Type':'text/html'},'body':'<html>synthetic lost receipt</html>','encoding':'text'}
            binary=not response.is_json and not response.mimetype.startswith('text/')
            return {'status':response.status_code,'headers':dict(response.headers),'body':base64.b64encode(response.data).decode() if binary else response.get_data(as_text=True),'encoding':'base64' if binary else 'text'}
        raise ValueError('unexpected native action '+action)
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
        def copyfile(self,source,outputfile):
            try:super().copyfile(source,outputfile)
            except (ConnectionAbortedError,ConnectionResetError,BrokenPipeError):pass
        def end_headers(self):
            self.send_header('Content-Security-Policy',"default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' blob:; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'none'")
            super().end_headers()
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(QuietHandler,directory=str(ROOT/'clients/frontend/dist')))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    output=ROOT/'.build-tools/client-ui-tests';output.mkdir(parents=True,exist_ok=True)
    errors=[];remote_requests=[];results=[]
    def wait_until(page,expression):
        end=time.monotonic()+20
        while time.monotonic()<end:
            if page.evaluate('()=>('+expression+')'):return
            page.wait_for_timeout(100)
        raise AssertionError('Condition timed out: '+expression)
    def wait_theme_initialized(page):
        # An iframe's heading can render before deferred theme.js binds its
        # handlers. Clicking before DOMContentLoaded silently drops the action.
        wait_until(page, """(
            window.XTYF.route==='/account' &&
            document.querySelector('#page-host iframe')?.contentDocument
              ?.querySelector('[data-theme-toggle]')?.title?.startsWith('切换为')
        )""")
    with sync_playwright() as p:
        print('Launching Chromium...',flush=True)
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1280,'height':900})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('request',lambda request:remote_requests.append(request.url) if request.url.startswith('http') and not request.url.startswith('http://127.0.0.1:') else None)
        page.expose_function('__nativeTest',native)
        page.add_init_script("window.XTYFNative={invoke:(action,args)=>window.__nativeTest(action,args)};")
        try:
            page.goto(f'http://127.0.0.1:{server.server_port}/index.html')
            wait_until(page,'window.XTYF?.compatible')
            frame=page.frame_locator('#page-host iframe')
            frame.locator('input[name=username]').fill('admin')
            frame.locator('input[name=password]').fill('ci-test-password-only')
            frame.locator('button[type=submit]').click()
            wait_until(page,"window.XTYF.route==='/' && window.XTYF.bootstrap.authenticated")
            frame.locator('h1').first.wait_for();page.wait_for_timeout(500)
            results.append('JSON login and dashboard through native bridge')
            print(results[-1],flush=True)
            frame.locator('.ux-skip-link').evaluate('el=>el.click()')
            assert frame.locator('#main-content').evaluate('el=>document.activeElement===el')
            assert page.locator('#page-host iframe').get_attribute('srcdoc')
            results.append('local skip link keeps packaged page and keyboard focus')
            pages=[('/tasks','任务中心'),('/tasks?type=daily','任务中心'),('/manage','任务管理'),('/members','成员管理'),('/discussions/','协作'),('/funds/','资金'),('/invoices/','发票'),('/account','账户'),('/admin/mail','邮件'),('/admin/security','权限')]
            for path,label in pages:
                page.evaluate('(path)=>XTYF.navigate(path)',path)
                frame.locator('h1').first.wait_for();page.wait_for_timeout(400)
                text=frame.locator('body').inner_text()
                assert label in text,(path,text[:200])
                assert page.locator('#page-host iframe').get_attribute('srcdoc'),path
                if path=='/funds/':
                    frame.locator('[data-bs-target="#t-add"]').click()
                    frame.locator('#aT').select_option('进')
                    frame.locator('#aAmt').fill('12.34')
                    frame.locator('#aNotes').fill('synthetic native funds recovery')
                    state['drop_funds_response']=True
                    frame.locator('#addF [type=submit]').click()
                    frame.locator('#funds-create-status').filter(has_text='结果待确认').wait_for()
                    page.evaluate("XTYF.navigate('/funds/',true)")
                    frame.locator('[data-bs-target="#t-add"]').click()
                    frame.locator('#funds-create-retry').wait_for()
                    assert frame.locator('#aNotes').input_value()=='synthetic native funds recovery'
                    frame.locator('#funds-create-retry').click()
                    frame.locator('#funds-create-fields:enabled').wait_for()
                    writes=[json.loads(args['body']) for action,args in state['calls']
                            if action=='request' and args.get('path')=='/funds/api/records' and args.get('method')=='POST']
                    assert len(writes)==2 and writes[0]==writes[1],writes
                    records=client.get('/funds/api/records').get_json()['data']
                    assert sum(r['notes']=='synthetic native funds recovery' for r in records)==1
                    results.append('packaged funds creation recovers immutable receipts through authenticated native IPC')
                if path=='/account':
                    assert frame.locator('#email-form').is_visible()
                    assert frame.locator('#email-code').get_attribute('pattern')=='[0-9]{6}'
                    results.append('account contains the current six-digit email verification controls')
                if path=='/manage':
                    frame.locator('#schedule-project:enabled').wait_for()
                    frame.locator('#schedule-project').click()
                    frame.locator('#schedule-project-choice [name=project]').select_option(index=1)
                    frame.locator('#schedule-project-choice [type=submit]').click()
                    frame.locator('#schedule-summary').filter(has_text='参与').wait_for()
                    frame.locator('[data-schedule-close]').click()
                    results.append('local schedule chooser loads preview through authenticated native transport')
                results.append('local route '+path)
                print(results[-1],flush=True)
            page.evaluate("XTYF.navigate('/discussions/')")
            frame.locator('#intro-edit').click()
            frame.locator('#intro-form [name=bio]').fill('合成客户端简介')
            frame.locator('#intro-add-profile').click()
            frame.locator('#intro-profile-links [data-link-url]').fill('www.github.com/example-client')
            frame.locator('#intro-add-repository').click()
            frame.locator('#intro-repository-links [data-link-url]').fill('github.com/example-client/robot')
            frame.locator('#intro-repository-links [data-link-description]').fill('合成仓库介绍')
            frame.locator('#intro-save').click()
            frame.locator('#intro-save-result').filter(has_text='简介已保存').wait_for()
            frame.locator('#intro-close').click()
            page.evaluate("XTYF.navigate('/account')")
            frame.locator('#intro-edit').click()
            frame.locator('#intro-dialog[open]').wait_for()
            assert frame.locator('#intro-form [name=bio]').input_value()=='合成客户端简介'
            assert frame.locator('#intro-profile-links [data-link-url]').input_value()=='www.github.com/example-client'
            frame.locator('#intro-close').click()
            assert frame.locator('#intro-preview a').count()==2
            assert '合成仓库介绍' in frame.locator('#intro-preview').inner_text()
            results.append('shared introduction editor and link preview persist through authenticated native transport')
            page.evaluate("XTYF.navigate('/account/email/verify#token=test')")
            page.get_by_role('heading',name='本地页面不存在').wait_for()
            results.append('removed legacy email-link template uses the local unavailable-page message')
            page.evaluate("XTYF.navigate('/account')")
            wait_theme_initialized(page)
            frame.locator('[data-theme-toggle]').first.click()
            wait_until(page,"window.XTYF.bootstrap.me.theme_mode==='dark'")
            assert frame.locator('html').get_attribute('data-theme')=='dark'
            page.evaluate("XTYF.navigate('/tasks')")
            frame.locator('h1').first.wait_for()
            assert frame.locator('html').get_attribute('data-theme')=='dark'
            assert page.evaluate('()=>XTYFClient.back()')
            wait_theme_initialized(page)
            frame.locator('[data-theme-toggle]').first.click()
            wait_until(page,"window.XTYF.bootstrap.me.theme_mode==='light'")
            assert frame.locator('html').get_attribute('data-theme')=='light'
            results.append('theme persists across local routes and native back restores previous page')
            page.evaluate("XTYF.navigate('/invoices/')")
            frame.locator('#fileInput').wait_for(state='attached')
            frame.locator('#fileInput').evaluate("el=>{el.onchange=null;el.click();}")
            wait_until(page,"document.querySelector('#page-host iframe').contentDocument.getElementById('fileInput').files.length===1")
            multipart=frame.locator('body').evaluate("async()=>{const fd=new FormData();fd.append('note','测试');fd.append('file',document.getElementById('fileInput').files[0]);return (await fetch('/__upload_test',{method:'POST',body:fd})).json();}")
            assert multipart['fields']==[{'name':'note','value':'测试'}],multipart
            assert multipart['files'][0]['handle']=='test-file-1',multipart
            results.append('native file handles and multipart fields')
            frame.locator('body').evaluate("()=>{const link=document.createElement('a');link.href='https://example.com/help';document.body.append(link);link.click();}")
            page.wait_for_timeout(100)
            assert any(a=='open_external' and b['url']=='https://example.com/help' for a,b in state['calls'])
            results.append('external link routed to isolated native view')
            page.evaluate("()=>XTYF.preview('/__preview.pdf','测试 PDF')")
            pdf_frame=page.frame_locator('#preview-content iframe')
            pdf_frame.locator('canvas').wait_for();pdf_frame.locator('#status').wait_for(state='hidden')
            assert pdf_frame.locator('#page-number').inner_text()=='1 / 1'
            page.locator('#preview-close').click()
            results.append('bundled PDF.js renders authenticated PDF under production CSP')
            page.evaluate("XTYF.navigate('/tasks')")
            frame.locator('h1').first.wait_for();page.wait_for_timeout(400)
            page.screenshot(path=str(output/'desktop-tasks.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(200)
            page.screenshot(path=str(output/'mobile-tasks.png'),full_page=True)
            assert frame.locator('body').evaluate('()=>document.documentElement.scrollWidth<=innerWidth+1')
            results.append('desktop and 390px mobile layout')
            page.evaluate("XTYF.navigate('/account')")
            frame.locator('h1').first.wait_for();page.wait_for_timeout(150)
            frame.locator('[data-ws-menu]').click()
            frame.locator('form[action="/logout"] button').click()
            wait_until(page,"window.XTYF.route.startsWith('/login') && !window.XTYF.bootstrap.authenticated")
            results.append('JSON logout returns local login')
            state['offline']=True;page.reload();frame.locator('#login-form').wait_for()
            page.locator('#connection-status').wait_for(state='visible')
            page.locator('#client-settings-open').click();page.locator('#server-url').wait_for(state='visible');page.locator('#settings-cancel').click()
            results.append('offline login and server settings')
            state['offline']=False;state['legacy']=True;page.locator('#connection-retry').click()
            page.locator('#connection-status').filter(has_text='当前输入已保留').wait_for()
            assert frame.locator('#login-form').is_visible()
            results.append('incompatible retry preserves the existing local form')
            page.reload()
            page.get_by_role('heading',name='服务器需要更新').wait_for()
            results.append('old server rejected without loading remote HTML')
            for legacy in ('html200','array200','object200'):
                state['legacy']=legacy;page.reload()
                page.get_by_role('heading',name='服务器需要更新').wait_for()
                results.append('successful incompatible bootstrap '+legacy+' requires server update')
            state['legacy']='503';page.reload()
            page.locator('#connection-status').wait_for(state='visible')
            assert not page.get_by_role('heading',name='服务器需要更新').count()
            assert page.frame_locator('#page-host iframe').locator('#login-form').is_visible()
            results.append('HTTP503 remains connection failure with local login')
            state['legacy']=False
            delayed=browser.new_page(viewport={'width':900,'height':700})
            delayed.on('pageerror',lambda error:errors.append(str(error)))
            delayed.expose_function('__nativeTest',native)
            old={'ok':True,'protocolVersion':1,'authenticated':True,'me':{'id':999,'name':'OLD SERVER','role':'admin','ui_theme':'dark'},'user':{'id':999},'permissions':{'manage_security':True},'statuses':['OLD OPTION'],'priorities':['OLD PRIORITY']}
            delayed.add_init_script("let held=false;window.XTYFNative={invoke:(action,args)=>{if(action==='request'&&args.path==='/api/client/bootstrap'&&!held){held=true;return new Promise(resolve=>{window.__releaseOld=()=>resolve({status:200,headers:{'Content-Type':'application/json'},body:"+json.dumps(json.dumps(old))+",encoding:'text'});});}return window.__nativeTest(action,args);}};")
            delayed.goto(f'http://127.0.0.1:{server.server_port}/index.html')
            wait_until(delayed,"typeof window.__releaseOld==='function'")
            delayed.locator('#client-settings-open').click();delayed.locator('#server-url').fill('https://replacement.invalid')
            delayed.locator('#server-form button[type=submit]').click()
            wait_until(delayed,"XTYF.compatible && XTYF.config.serverUrl==='https://replacement.invalid'")
            delayed.evaluate('()=>window.__releaseOld()');delayed.wait_for_timeout(200)
            assert delayed.evaluate('()=>XTYF.bootstrap.me') is None
            assert delayed.evaluate('()=>XTYF.bootstrap.authenticated') is False
            assert 'OLD OPTION' not in delayed.evaluate('()=>XTYF.bootstrap.statuses')
            assert delayed.frame_locator('#page-host iframe').locator('#login-form').is_visible()
            delayed.close();results.append('server switch supersedes pending bootstrap and ignores late old user/options')
            assert not remote_requests,remote_requests
            assert not errors,errors
            (output/'result.json').write_text(json.dumps({'passed':results,'pageErrors':errors,'remoteWebRequests':remote_requests},ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'passed':len(results),'pageErrors':errors,'remoteWebRequests':remote_requests,'report':str(output/'result.json')},ensure_ascii=False))
        except Exception:
            page.screenshot(path=str(output/'failure.png'),full_page=True)
            print('PAGE_ERRORS',errors)
            raise
        finally:browser.close();server.shutdown()


if __name__=='__main__':run()
