package online.skywalker.manager;

import android.app.*;
import android.content.*;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.view.*;
import android.webkit.*;
import android.webkit.CookieManager;
import android.widget.*;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.ExecutorService;

/** Remote website has no native API bridge. Its sole document port receives one chosen Blob download. */
final class WebsiteView {
    interface Host {
        void configure(WebView view);
        void external(String url);
        void local(String intent);
        void message(String message);
        boolean choose(WebView view, ValueCallback<Uri[]> callback, WebChromeClient.FileChooserParams params);
        void cancelChooser(WebView view);
        void save(WebsiteView owner, File staged, String name, String mime, long generation) throws IOException;
    }
    private final Activity activity;
    private final Host host;
    private final ExecutorService workers;
    private final String origin;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Dialog dialog;
    private final WebView web;
    private final LinearLayout controls;
    private final Button menu;
    private volatile long generation;
    private volatile boolean closed;
    private final Set<HttpURLConnection> connections = Collections.synchronizedSet(new HashSet<HttpURLConnection>());
    private WebMessagePort blobPort;
    private String blobRequest;
    private File blobFile;
    private OutputStream blobOutput;
    private long blobExpected, blobWritten;
    private String blobName, blobMime;
    private final Set<File> stagedFiles = Collections.synchronizedSet(new HashSet<File>());

    WebsiteView(Activity activity, Host host, ExecutorService workers, String origin) {
        this.activity = activity; this.host = host; this.workers = workers; this.origin = origin;
        dialog = new Dialog(activity, android.R.style.Theme_Material_Light_NoActionBar);
        FrameLayout root = new FrameLayout(activity);
        root.setOnApplyWindowInsetsListener((v, insets) -> { v.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(), insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom()); return insets; });
        LinearLayout content = new LinearLayout(activity); content.setOrientation(LinearLayout.VERTICAL);
        controls = new LinearLayout(activity); controls.setOrientation(LinearLayout.VERTICAL);
        LinearLayout actions = new LinearLayout(activity); HorizontalScrollView scroll = new HorizontalScrollView(activity); scroll.addView(actions);
        web = new WebView(activity); host.configure(web);
        web.getSettings().setSupportMultipleWindows(true);
        actions.addView(button("返回", () -> { if (web.canGoBack()) web.goBack(); else leave("return"); }));
        actions.addView(button("首页", () -> confirm(() -> web.loadUrl(origin + "/"))));
        actions.addView(button("重载", () -> confirm(web::reload)));
        actions.addView(button("设置", () -> leave("settings")));
        actions.addView(button("本地界面", () -> leave("retry")));
        TextView address = new TextView(activity); address.setText("网站模式 · " + origin); address.setPadding(12, 4, 12, 4);
        controls.addView(scroll); controls.addView(address);
        content.addView(controls); content.addView(web, new LinearLayout.LayoutParams(-1, 0, 1));
        root.addView(content, new FrameLayout.LayoutParams(-1, -1));
        menu = button("菜单", () -> setControlsVisible(controls.getVisibility() != View.VISIBLE));
        menu.setMinHeight(dp(48)); menu.setMinimumHeight(dp(48));
        FrameLayout.LayoutParams menuLayout = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.END);
        menuLayout.setMargins(dp(12), dp(12), dp(12), dp(12));
        root.addView(menu, menuLayout); setControlsVisible(false); dialog.setContentView(root);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (WebsitePolicy.trusted(origin, request.getUrl().toString())) return false;
                if (request.isForMainFrame()) host.external(request.getUrl().toString()); return true;
            }
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                // Remote pages can never read private bundled/signed resources.
                if ("appassets.androidplatform.net".equals(request.getUrl().getHost())) return new WebResourceResponse("text/plain", "UTF-8", 403, "Forbidden", Collections.emptyMap(), new ByteArrayInputStream(new byte[0]));
                return null;
            }
            @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) {
                invalidate();
                if (!WebsitePolicy.trusted(origin, url)) { view.stopLoading(); host.external(url); }
            }
            @Override public void onPageFinished(WebView view, String url) {
                if (WebsitePolicy.trusted(origin, url) && !closed) { CookieManager.getInstance().flush(); attachBlobPort(); }
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) host.message("网站暂时无法打开，请重试或返回设置：" + error.getDescription());
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (!WebsitePolicy.trusted(origin, view.getUrl())) { callback.onReceiveValue(null); return true; }
                return host.choose(view, callback, params);
            }
            @Override public boolean onJsAlert(WebView view, String url, String text, JsResult result) {
                new AlertDialog.Builder(activity).setMessage(text).setPositiveButton("确定", (d,w) -> result.confirm()).setOnCancelListener(d -> result.cancel()).show(); return true;
            }
            @Override public boolean onJsConfirm(WebView view, String url, String text, JsResult result) {
                new AlertDialog.Builder(activity).setMessage(text).setPositiveButton("确定", (d,w) -> result.confirm()).setNegativeButton("取消", (d,w) -> result.cancel()).setOnCancelListener(d -> result.cancel()).show(); return true;
            }
            @Override public boolean onCreateWindow(WebView view, boolean isDialog, boolean gesture, android.os.Message result) {
                if (!gesture) return false;
                WebView popup = new WebView(activity); host.configure(popup);
                popup.setWebViewClient(new WebViewClient() {
                    private void route(String url) {
                        if (url == null || "about:blank".equals(url)) return;
                        if (WebsitePolicy.trusted(origin, url)) web.loadUrl(url); else host.external(url);
                        popup.stopLoading(); popup.destroy();
                    }
                    @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest request) { route(request.getUrl().toString()); return true; }
                    @Override public void onPageStarted(WebView v, String url, android.graphics.Bitmap icon) { route(url); }
                });
                ((WebView.WebViewTransport)result.obj).setWebView(popup); result.sendToTarget(); return true;
            }
        });
        web.setDownloadListener((url, agent, disposition, mime, length) -> download(url, URLUtil.guessFileName(url, disposition, mime), mime, agent));
        dialog.setOnKeyListener((d,key,event) -> {
            if (key != KeyEvent.KEYCODE_BACK) return false;
            if (event.getAction() == KeyEvent.ACTION_UP && !event.isCanceled()) {
                if (controls.getVisibility() == View.VISIBLE) setControlsVisible(false);
                else if (web.canGoBack()) web.goBack();
                else leave("return");
            }
            return true;
        });
        dialog.setOnDismissListener(d -> { closed = true; invalidate(); web.destroy(); });
    }
    private int dp(int value) { return Math.round(value * activity.getResources().getDisplayMetrics().density); }
    private void setControlsVisible(boolean visible) {
        // Keep the same WebView and document: toggling the menu must not navigate or discard drafts.
        controls.setVisibility(visible ? View.VISIBLE : View.GONE);
        menu.setText(visible ? "收起菜单" : "菜单");
        menu.setContentDescription(visible ? "收起客户端菜单" : "展开客户端菜单");
    }
    private Button button(String text, Runnable action) { Button button = new Button(activity); button.setText(text); button.setAllCaps(false); button.setOnClickListener(v -> action.run()); return button; }
    private void confirm(Runnable action) {
        new AlertDialog.Builder(activity).setMessage("未保存的内容可能丢失，是否继续？").setPositiveButton("继续", (d,w) -> action.run()).setNegativeButton("取消", null).show();
    }
    private void leave(String intent) { confirm(() -> { close(); host.local(intent); }); }
    void show() { dialog.show(); dialog.getWindow().setLayout(-1, -1); web.loadUrl(origin + "/"); }
    void close() { if (!closed) dialog.dismiss(); }
    void pause() { web.onPause(); CookieManager.getInstance().flush(); }
    void resume() { if (!closed) web.onResume(); }
    boolean valid(long expected) { return !closed && expected == generation; }
    private void invalidate() {
        generation++; host.cancelChooser(web);
        synchronized (connections) { for (HttpURLConnection connection : connections) connection.disconnect(); connections.clear(); }
        if (blobPort != null) { blobPort.close(); blobPort = null; } resetBlob();
        synchronized (stagedFiles) { for (File file : stagedFiles) file.delete(); stagedFiles.clear(); }
    }
    void consumed(File file) { stagedFiles.remove(file); file.delete(); }
    private void finish(File file, String name, String mime, long expected) {
        handler.post(() -> {
            if (!valid(expected)) { consumed(file); return; }
            try { host.save(this, file, WebsitePolicy.filename(name), mime, expected); }
            catch (IOException error) { consumed(file); host.message(error.getMessage()); }
        });
    }
    private void download(String url, String name, String mime, String agent) {
        if (!WebsitePolicy.trusted(origin, web.getUrl())) return;
        if (WebsitePolicy.blob(origin, url)) { beginBlob(url, name); return; }
        if (!WebsitePolicy.trusted(origin, url)) { host.message("仅允许保存当前网站的附件"); return; }
        final long expected = generation;
        // Read only website cookies; the native API session is never exported into this view.
        final String cookie = CookieManager.getInstance().getCookie(url);
        workers.execute(() -> {
            File staged = null;
            try {
                URI target = URI.create(url);
                for (int redirects = 0; redirects < 6; redirects++) {
                    if (!valid(expected)) throw new InterruptedIOException("页面或服务器已切换，下载已取消");
                    HttpURLConnection current = (HttpURLConnection)target.toURL().openConnection(); connections.add(current);
                    try {
                        current.setConnectTimeout(20000); current.setReadTimeout(180000); current.setInstanceFollowRedirects(false);
                        if (cookie != null) current.setRequestProperty("Cookie", cookie); current.setRequestProperty("User-Agent", agent);
                        int status = current.getResponseCode(); String location = current.getHeaderField("Location");
                        if (Arrays.asList(301,302,303,307,308).contains(status) && location != null) {
                            target = target.resolve(location); if (!WebsitePolicy.trusted(origin, target.toString())) throw new IOException("附件重定向到其他网站，已取消保存"); continue;
                        }
                        String type = current.getContentType();
                        if (status < 200 || status >= 300 || type != null && type.toLowerCase(Locale.ROOT).contains("text/html")) throw new IOException("附件下载失败，请检查网站登录状态（" + status + "）");
                        long length = current.getContentLengthLong(); if (length >= 0) WebsitePolicy.size(length);
                        staged = File.createTempFile("website-download-", ".tmp", activity.getCacheDir()); stagedFiles.add(staged);
                        try (InputStream input = current.getInputStream(); OutputStream output = new FileOutputStream(staged)) {
                            byte[] buffer = new byte[65536]; long count = 0; int n;
                            while ((n = input.read(buffer)) != -1) { count += n; WebsitePolicy.size(count); if (!valid(expected)) throw new InterruptedIOException("下载已取消"); output.write(buffer, 0, n); }
                        }
                        finish(staged, name, mime == null ? "application/octet-stream" : mime, expected); return;
                    } finally { connections.remove(current); current.disconnect(); }
                }
                throw new IOException("附件重定向次数过多");
            } catch (Exception error) { if (staged != null) consumed(staged); if (valid(expected)) handler.post(() -> host.message(error.getMessage())); }
        });
    }
    private void resetBlob() {
        if (blobOutput != null) try { blobOutput.close(); } catch (IOException ignored) {}
        if (blobFile != null) consumed(blobFile); blobFile = null; blobOutput = null; blobRequest = null;
    }
    private void beginBlob(String url, String name) {
        if (blobPort == null || blobRequest != null) { host.message("请等待当前页面或下载完成后重试"); return; }
        blobRequest = UUID.randomUUID().toString(); blobName = WebsitePolicy.filename(name);
        web.evaluateJavascript("window.__xtyfWebsiteBlob(" + JSONObject.quote(blobRequest) + "," + JSONObject.quote(url) + ")", null);
    }
    private void attachBlobPort() {
        if (blobPort != null) return;
        final long expected = generation;
        // Dedicated, top-document port with per-download nonce; no arbitrary file paths, requests or native commands.
        String script = "(()=>{let port,acks=[];addEventListener('message',e=>{if(e.data!=='xtyf-website-download'||!e.ports[0])return;port=e.ports[0];port.onmessage=()=>acks.shift()?.();});"
            + "window.__xtyfWebsiteBlob=async(id,url)=>{const send=x=>new Promise(resolve=>{acks.push(resolve);port.postMessage(JSON.stringify(Object.assign({id},x)));});"
            + "try{const b=await(await fetch(url)).blob();await send({op:'begin',size:b.size,mime:b.type});for(let n=0;n<b.size;n+=49152){const bytes=new Uint8Array(await b.slice(n,n+49152).arrayBuffer());let s='';for(const v of bytes)s+=String.fromCharCode(v);await send({op:'chunk',data:btoa(s)});}await send({op:'end'});}catch(e){port.postMessage(JSON.stringify({id,op:'error'}));}};})();";
        web.evaluateJavascript(script, ignored -> {
            if (!valid(expected) || !WebsitePolicy.trusted(origin, web.getUrl())) return;
            WebMessagePort[] ports = web.createWebMessageChannel(); blobPort = ports[0];
            ports[0].setWebMessageCallback(new WebMessagePort.WebMessageCallback() {
                @Override public void onMessage(WebMessagePort channel, WebMessage message) {
                    if (!valid(expected) || channel != blobPort || !WebsitePolicy.trusted(origin, web.getUrl())) return;
                    try {
                        String data = message.getData(); if (data == null || data.length() > 70000) throw new IOException("下载数据块无效");
                        JSONObject value = new JSONObject(data); if (blobRequest == null || !blobRequest.equals(value.optString("id"))) return;
                        String op = value.getString("op");
                        if ("begin".equals(op) && blobOutput == null) {
                            blobExpected = value.getLong("size"); WebsitePolicy.size(blobExpected); blobWritten = 0; blobMime = value.optString("mime", "application/octet-stream");
                            blobFile = File.createTempFile("website-blob-", ".tmp", activity.getCacheDir()); stagedFiles.add(blobFile); blobOutput = new FileOutputStream(blobFile);
                        } else if ("chunk".equals(op) && blobOutput != null) {
                            byte[] bytes = Base64.getDecoder().decode(value.getString("data")); if (bytes.length > 49152 || blobWritten + bytes.length > blobExpected) throw new IOException("下载数据大小不符");
                            blobOutput.write(bytes); blobWritten += bytes.length;
                        } else if ("end".equals(op) && blobOutput != null && blobWritten == blobExpected) {
                            blobOutput.close(); blobOutput = null; File ready = blobFile; blobFile = null; blobRequest = null; finish(ready, blobName, blobMime.isEmpty() ? "application/octet-stream" : blobMime, expected);
                        } else throw new IOException("下载中断，请重试");
                        channel.postMessage(new WebMessage("ok"));
                    } catch (Exception error) { resetBlob(); host.message(error.getMessage()); }
                }
            });
            web.postWebMessage(new WebMessage("xtyf-website-download", new WebMessagePort[]{ports[1]}), Uri.parse(origin));
        });
    }
}
