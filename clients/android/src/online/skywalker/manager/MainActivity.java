package online.skywalker.manager;

import android.app.*;
import android.content.*;
import android.database.Cursor;
import android.graphics.Color;
import android.net.Uri;
import android.os.*;
import android.provider.OpenableColumns;
import android.view.*;
import android.webkit.*;
import android.widget.*;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;

/** The main WebView contains bundled or signed private assets only. Credentials stay native. */
public class MainActivity extends Activity {
    private static final String LOCAL_ORIGIN = "https://appassets.androidplatform.net";
    private static final int PICK_FILE = 10, SAVE_FILE = 11;
    private WebView web, externalWeb;
    private Dialog externalDialog;
    private volatile WebMessagePort port;
    private volatile NativeHttpClient client;
    private UiUpdateStore updates;
    private LinearLayout mainRoot;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private int viewGeneration;
    private String server;
    private final ExecutorService workers = Executors.newFixedThreadPool(4);
    private final Map<String, Uri> files = new ConcurrentHashMap<>();
    private Pending picker, download;
    private ValueCallback<Uri[]> legacyPicker;
    private boolean destroyed;
    private static final class Pending {
        final Object id; final JSONObject args; final WebMessagePort channel; final NativeHttpClient client; final UiUpdateStore updates;
        Pending(Object id, JSONObject args, WebMessagePort channel, NativeHttpClient client, UiUpdateStore updates) { this.id=id; this.args=args; this.channel=channel; this.client=client; this.updates=updates; }
    }
    private int dp(int n) { return Math.round(n * getResources().getDisplayMetrics().density); }
    private void message(String text) { Toast.makeText(this, text, Toast.LENGTH_LONG).show(); }
    private Button button(String title, Runnable action) {
        Button button = new Button(this); button.setText(title); button.setAllCaps(false); button.setOnClickListener(v -> action.run()); return button;
    }
    private void connect(String value) throws IOException {
        String normalized = NativeHttpClient.normalize(value); if (client != null) client.cancelAll(); if (updates != null) updates.retire();
        String key = "native.session.v1." + java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(normalized.getBytes(StandardCharsets.UTF_8));
        SharedPreferences prefs = getPreferences(MODE_PRIVATE);
        client = new NativeHttpClient(normalized, prefs.getString(key, ""), saved -> prefs.edit().putString(key, saved).apply());
        updates = new UiUpdateStore(new File(getFilesDir(), "ui-updates"), normalized, BuildConfig.VERSION,
            new String(UiUpdateStore.read(getAssets().open("update-public-key.pem"), 16384), StandardCharsets.US_ASCII), client);
        server = normalized; prefs.edit().putString("server", normalized).apply(); files.clear();
    }
    private boolean isLocal(Uri uri) {
        return "https".equals(uri.getScheme()) && "appassets.androidplatform.net".equals(uri.getHost()) && uri.getPort() == -1 && uri.getUserInfo() == null;
    }
    private boolean isRoot(String url) {
        if (url == null) return false;
        Uri uri = Uri.parse(url); return isLocal(uri) && ("/index.html".equals(uri.getPath()) || "/".equals(uri.getPath()));
    }
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        try { connect(getPreferences(MODE_PRIVATE).getString("server", BuildConfig.SERVER_URL)); }
        catch (IOException error) { try { connect(BuildConfig.SERVER_URL); } catch (IOException invalid) { throw new IllegalStateException(invalid); } }
        mainRoot = new LinearLayout(this); mainRoot.setOrientation(LinearLayout.VERTICAL); mainRoot.setBackgroundColor(Color.rgb(245,247,251));
        mainRoot.setOnApplyWindowInsetsListener((v, insets) -> { v.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(), insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom()); return insets; });
        setContentView(mainRoot);
        // Legacy WebView login cookies are deliberately not imported. HTTP sessions now live only in the native transport.
        CookieManager.getInstance().setAcceptCookie(false); CookieManager.getInstance().removeAllCookies(null);
        try { createMainView(updates.startView(true)); } catch (IOException error) { message(error.getMessage()); createMainView(null); }
    }
    private void createMainView(final File resourceRoot) {
        closePort(); mainHandler.removeCallbacksAndMessages(null); int generation = ++viewGeneration;
        if (web != null) { mainRoot.removeView(web); web.destroy(); }
        files.clear(); web = new WebView(this); configure(web); mainRoot.addView(web, new LinearLayout.LayoutParams(-1, -1));
        final UiUpdateStore owner = updates;
        web.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) { return asset(request.getUrl(), request.getMethod(), resourceRoot); }
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (isLocal(request.getUrl())) return false;
                if (request.isForMainFrame()) openExternal(request.getUrl().toString()); return true;
            }
            @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) { if (view == web) closePort(); }
            @Override public void onPageFinished(WebView view, String url) { if (view == web && isRoot(url) && port == null) attachPort(); }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) { if (request.isForMainFrame()) message("本地页面无法打开：" + error.getDescription()); }
        });
        web.setWebChromeClient(chrome(true)); web.setDownloadListener((url, agent, disposition, mime, length) -> message("请使用页面中的保存按钮下载文件"));
        web.loadUrl(LOCAL_ORIGIN + "/index.html");
        if (resourceRoot != null) mainHandler.postDelayed(() -> {
            if (!destroyed && generation == viewGeneration && owner == updates) {
                try { message("页面未正常启动，正在恢复可用版本"); createMainView(owner.failView()); }
                catch (IOException error) { message(error.getMessage()); createMainView(null); }
            }
        }, 15000);
    }
    private void configure(WebView view) {
        WebSettings settings = view.getSettings(); settings.setJavaScriptEnabled(true); settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false); settings.setAllowContentAccess(false); settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setJavaScriptCanOpenWindowsAutomatically(false); settings.setSupportMultipleWindows(false);
        settings.setSupportZoom(true); settings.setBuiltInZoomControls(true); settings.setDisplayZoomControls(false); settings.setUseWideViewPort(true); settings.setLoadWithOverviewMode(true);
        settings.setUserAgentString(settings.getUserAgentString() + " XTYFManager/" + BuildConfig.VERSION); CookieManager.getInstance().setAcceptThirdPartyCookies(view, false);
    }
    private WebResourceResponse asset(Uri uri, String method, File resourceRoot) {
        if (!isLocal(uri) || !"GET".equals(method)) return denied(403, "Forbidden");
        String path = uri.getPath(); if ("/".equals(path)) path = "/index.html";
        if (path == null || path.indexOf('\\') >= 0 || path.indexOf('\0') >= 0 || Arrays.asList(path.split("/")).contains("..")) return denied(403, "Forbidden");
        try {
            Map<String, String> headers = new HashMap<>(); headers.put("Cache-Control", "no-cache"); headers.put("X-Content-Type-Options", "nosniff");
            headers.put("Content-Security-Policy", "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' blob:; style-src 'self' 'unsafe-inline'; connect-src 'self' ipc: http://ipc.localhost; frame-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'none'");
            String name = UiUpdateStore.assetPath(path.substring(1));
            InputStream input = resourceRoot == null ? getAssets().open("frontend/" + name) : new FileInputStream(new File(resourceRoot, name));
            return new WebResourceResponse(mime(path), "UTF-8", 200, "OK", headers, input);
        } catch (IOException error) { return denied(404, "Not Found"); }
    }
    private static String mime(String name) {
        String extension = name.substring(name.lastIndexOf('.') + 1).toLowerCase(Locale.ROOT);
        switch (extension) {
            case "html": return "text/html"; case "js": case "mjs": return "text/javascript"; case "css": return "text/css";
            case "json": case "map": return "application/json"; case "svg": return "image/svg+xml"; case "wasm": return "application/wasm";
            case "woff": return "font/woff"; case "woff2": return "font/woff2"; case "pdf": return "application/pdf";
            default: String result = MimeTypeMap.getSingleton().getMimeTypeFromExtension(extension); return result == null ? "application/octet-stream" : result;
        }
    }
    private static WebResourceResponse denied(int status, String reason) {
        return new WebResourceResponse("text/plain", "UTF-8", status, reason, Collections.emptyMap(), new ByteArrayInputStream(reason.getBytes(StandardCharsets.UTF_8)));
    }
    private void closePort() { if (port != null) { port.close(); port = null; } }
    private void attachPort() {
        closePort(); WebMessagePort[] pair = web.createWebMessageChannel(); port = pair[0];
        port.setWebMessageCallback(new WebMessagePort.WebMessageCallback() {
            @Override public void onMessage(WebMessagePort channel, WebMessage message) {
                if (channel != port || !isRoot(web.getUrl())) return;
                try {
                    JSONObject payload = new JSONObject(message.getData()); Object id = payload.get("id");
                    if (!(id instanceof String || id instanceof Number)) throw new JSONException("Invalid request id");
                    JSONObject args = payload.optJSONObject("args"); dispatch(payload.getString("action"), new Pending(id, args == null ? new JSONObject() : args, channel, client, updates));
                } catch (Exception error) { message("无效的本地请求：" + error.getMessage()); }
            }
        });
        web.postWebMessage(new WebMessage("xtyf-native-port", new WebMessagePort[]{pair[1]}), Uri.parse(LOCAL_ORIGIN));
    }
    private void reply(Pending request, Object result, Exception error) {
        runOnUiThread(() -> {
            if (destroyed || request.channel != port) return;
            try {
                JSONObject response = new JSONObject().put("id", request.id).put("result", error == null ? result : JSONObject.NULL)
                    .put("error", error == null ? JSONObject.NULL : (error.getMessage() == null ? "请求失败" : error.getMessage()));
                request.channel.postMessage(new WebMessage(response.toString()));
            } catch (JSONException | IllegalStateException ignored) { /* Navigation can close the previous document's channel. */ }
        });
    }
    private void dispatch(String action, Pending request) {
        try {
            switch (action) {
                case "get_config": reply(request, configuration(), null); break;
                case "set_server":
                    connect(request.args.getString("serverUrl")); reply(request, configuration(), null);
                    mainHandler.postDelayed(() -> { try { createMainView(updates.startView(true)); } catch (IOException error) { message(error.getMessage()); createMainView(null); } }, 150); break;
                case "update_status": reply(request, request.updates.status(), null); break;
                case "check_updates": workers.execute(() -> { try { reply(request, request.updates.check(request.args.optBoolean("retry", false)), null); } catch (Exception error) { reply(request, null, error); } }); break;
                case "apply_update":
                    reply(request, request.updates.status(), null);
                    mainHandler.postDelayed(() -> { if (request.updates == updates) try { createMainView(updates.startView(true)); } catch (IOException error) { message(error.getMessage()); } }, 150); break;
                case "ui_ready":
                    request.updates.ready(); ++viewGeneration; reply(request, request.updates.status(), null); break;
                case "read_selected_image": workers.execute(() -> { try { reply(request, selectedImage(request), null); } catch (Exception error) { reply(request, null, error); } }); break;
                case "pick_files": pickFiles(request); break;
                case "download": saveFile(request); break;
                case "open_external": openExternal(request.args.getString("url")); reply(request, new JSONObject(), null); break;
                case "cancel": request.client.cancel(request.args.getString("requestId")); reply(request, new JSONObject(), null); break;
                case "request": case "read_resource": workers.execute(() -> { try { reply(request, http(request, "read_resource".equals(action)), null); } catch (Exception error) { reply(request, null, error); } }); break;
                default: throw new IOException("不支持的本地操作");
            }
        } catch (Exception error) { reply(request, null, error); }
    }
    private JSONObject configuration() throws JSONException {
        return new JSONObject().put("serverUrl", server).put("version", BuildConfig.VERSION).put("bridgeVersion", 2)
            .put("capabilities", new JSONArray(Arrays.asList("ui_updates", "image_preview"))).put("platform", "android");
    }
    private JSONObject selectedImage(Pending request) throws Exception {
        Uri uri = files.get(request.args.getString("handle"));
        if (uri == null || request.client != client || request.channel != port) throw new IOException("文件选择已失效，请重新选择");
        InputStream input = getContentResolver().openInputStream(uri); if (input == null) throw new IOException("无法读取所选图片");
        byte[] bytes = UiUpdateStore.read(input, 10 * 1024 * 1024);
        return new JSONObject().put("body", Base64.getEncoder().encodeToString(bytes)).put("mime", UiUpdateStore.imageMime(bytes)).put("size", bytes.length);
    }
    private JSONObject http(Pending request, boolean binary) throws Exception {
        JSONObject args = request.args; String path = args.getString("path"), method = binary ? "GET" : args.optString("method", "GET");
        Map<String, String> headers = new LinkedHashMap<>(); JSONObject given = args.optJSONObject("headers");
        if (given != null) for (Iterator<String> names = given.keys(); names.hasNext();) { String name = names.next(); headers.put(name, given.getString(name)); }
        NativeHttpClient.Body body = null; JSONObject multipart = args.optJSONObject("multipart");
        if (multipart != null) { String boundary = "xtyf-" + UUID.randomUUID(); headers.put("Content-Type", "multipart/form-data; boundary=" + boundary); body = output -> writeMultipart(output, multipart, boundary); }
        else if (args.has("body") && !args.isNull("body")) { byte[] bytes = args.getString("body").getBytes(StandardCharsets.UTF_8); body = output -> output.write(bytes); }
        final JSONObject[] response = new JSONObject[1];
        request.client.request(path, method, headers, body, args.optString("requestId", request.id.toString()), (status, responseHeaders, input) -> {
            ByteArrayOutputStream output = new ByteArrayOutputStream(); NativeHttpClient.copy(input, output);
            try {
                byte[] bytes = output.toByteArray(); response[0] = new JSONObject().put("status", status).put("headers", new JSONObject(responseHeaders))
                    .put("body", binary ? java.util.Base64.getEncoder().encodeToString(bytes) : new String(bytes, StandardCharsets.UTF_8)).put("encoding", binary ? "base64" : "text");
            } catch (JSONException error) { throw new IOException(error); }
        });
        return response[0];
    }
    private static String quoted(String value) { return value.replace("\\", "_").replace("\"", "_").replace("\r", "_").replace("\n", "_"); }
    private void writeMultipart(OutputStream output, JSONObject multipart, String boundary) throws IOException {
        try {
            JSONArray fields = multipart.optJSONArray("fields"), entries = multipart.optJSONArray("files");
            if (fields != null) for (int index = 0; index < fields.length(); index++) {
                JSONObject field = fields.getJSONObject(index);
                output.write(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + quoted(field.getString("name")) + "\"\r\n\r\n" + field.getString("value") + "\r\n").getBytes(StandardCharsets.UTF_8));
            }
            if (entries != null) for (int index = 0; index < entries.length(); index++) {
                JSONObject file = entries.getJSONObject(index); Uri uri = files.get(file.getString("handle")); if (uri == null) throw new IOException("文件选择已失效，请重新选择");
                output.write(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + quoted(file.getString("field")) + "\"; filename=\"" + quoted(file.getString("name")) + "\"\r\nContent-Type: " + quoted(file.optString("mime", "application/octet-stream")) + "\r\n\r\n").getBytes(StandardCharsets.UTF_8));
                try (InputStream input = getContentResolver().openInputStream(uri)) { if (input == null) throw new IOException("无法读取所选文件"); NativeHttpClient.copy(input, output); }
                output.write("\r\n".getBytes(StandardCharsets.UTF_8));
            }
            output.write(("--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8));
        } catch (JSONException error) { throw new IOException("无效的上传参数", error); }
    }
    private void pickFiles(Pending request) throws IOException {
        if (picker != null || legacyPicker != null) throw new IOException("请先完成当前文件选择"); picker = request;
        try { startActivityForResult(fileIntent(request.args.optBoolean("multiple", false), request.args.opt("accept")), PICK_FILE); }
        catch (ActivityNotFoundException error) { picker = null; throw new IOException("无法打开文件选择器", error); }
    }
    private Intent fileIntent(boolean multiple, Object accept) {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*").putExtra(Intent.EXTRA_ALLOW_MULTIPLE, multiple);
        List<String> types = new ArrayList<>(); String acceptText = accept instanceof String ? (String) accept : "";
        if (accept instanceof JSONArray) { JSONArray array = (JSONArray)accept; for (int index=0; index<array.length(); index++) acceptText += "," + array.optString(index); }
        for (String part : acceptText.split(",")) { String type = part.trim(); if (type.startsWith(".")) type = MimeTypeMap.getSingleton().getMimeTypeFromExtension(type.substring(1)); if (type != null && type.contains("/")) types.add(type); }
        if (!types.isEmpty()) intent.putExtra(Intent.EXTRA_MIME_TYPES, types.toArray(new String[0])); return intent;
    }
    private void saveFile(Pending request) throws Exception {
        if (download != null) throw new IOException("请先完成当前保存选择"); request.client.resolve(request.args.getString("path")); download = request;
        if (request.args.has("expectedSha256")) {
            try { request.updates.validateInstaller(request.args.getString("path"), request.args.getString("expectedSha256")); }
            catch (Exception error) { download = null; throw error; }
        }
        String filename = request.args.optString("filename", "download").replaceAll("[\\\\/\r\n]", "_");
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType(mime(filename)).putExtra(Intent.EXTRA_TITLE, filename);
        try { startActivityForResult(intent, SAVE_FILE); } catch (ActivityNotFoundException error) { download = null; throw new IOException("无法打开保存窗口", error); }
    }
    private JSONObject selected(Uri uri) throws Exception {
        String handle = UUID.randomUUID().toString(), name = "upload"; long size = -1;
        try (Cursor cursor = getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE}, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                int nameColumn = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME), sizeColumn = cursor.getColumnIndex(OpenableColumns.SIZE);
                if (nameColumn >= 0) name = cursor.getString(nameColumn); if (sizeColumn >= 0 && !cursor.isNull(sizeColumn)) size = cursor.getLong(sizeColumn);
            }
        }
        files.put(handle, uri); String type = getContentResolver().getType(uri);
        return new JSONObject().put("handle", handle).put("name", name).put("size", size).put("mime", type == null ? "application/octet-stream" : type);
    }
    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == PICK_FILE) {
            Pending request = picker; picker = null; List<Uri> uris = new ArrayList<>();
            if (resultCode == RESULT_OK && data != null) {
                if (data.getClipData() != null) for (int index = 0; index < data.getClipData().getItemCount(); index++) uris.add(data.getClipData().getItemAt(index).getUri());
                else if (data.getData() != null) uris.add(data.getData());
            }
            if (legacyPicker != null) { legacyPicker.onReceiveValue(uris.isEmpty() ? null : uris.toArray(new Uri[0])); legacyPicker = null; }
            if (request != null && request.client == client && request.channel == port) try { JSONArray result = new JSONArray(); for (Uri uri : uris) result.put(selected(uri)); reply(request, result, null); } catch (Exception error) { reply(request, null, error); }
        }
        if (requestCode == SAVE_FILE && download != null) {
            Pending request = download; download = null;
            if (resultCode != RESULT_OK || data == null || data.getData() == null) { try { reply(request, new JSONObject().put("cancelled", true), null); } catch (JSONException ignored) {} return; }
            Uri destination = data.getData(); workers.execute(() -> {
                try {
                    request.client.request(request.args.getString("path"), "GET", Collections.emptyMap(), null, request.args.optString("requestId", request.id.toString()), (status, headers, input) -> {
                        String type = headers.getOrDefault("content-type", "");
                        if (status < 200 || status >= 300 || type.contains("text/html") || type.contains("application/json")) throw new IOException("服务器拒绝下载，请检查登录状态（" + status + "）");
                        if (request.args.has("expectedSha256")) {
                            File staged = File.createTempFile("xtyf-installer-", ".tmp", getCacheDir());
                            try {
                                String expected = request.args.optString("expectedSha256"), path = request.args.optString("path"); request.updates.validateInstaller(path, expected);
                                try (OutputStream output = new FileOutputStream(staged)) { NativeHttpClient.copy(input, output); }
                                if (!UiUpdateStore.hashFile(staged).equals(expected)) throw new IOException("安装包摘要校验失败，未保存文件");
                                if (request.client != client || request.channel != port) throw new IOException("页面或服务器已切换，下载已取消");
                                try (InputStream verified = new FileInputStream(staged); OutputStream output = getContentResolver().openOutputStream(destination, "wt")) {
                                    if (output == null) throw new IOException("无法写入所选位置"); NativeHttpClient.copy(verified, output);
                                }
                            } finally { staged.delete(); }
                        } else try (OutputStream output = getContentResolver().openOutputStream(destination, "wt")) { if (output == null) throw new IOException("无法写入所选位置"); NativeHttpClient.copy(input, output); }
                    }); reply(request, new JSONObject().put("cancelled", false).put("path", destination.toString()), null);
                } catch (Exception error) { reply(request, null, error); }
            });
        }
    }
    private WebChromeClient chrome(boolean local) {
        return new WebChromeClient() {
            @Override public boolean onJsAlert(WebView view, String url, String text, JsResult result) {
                new AlertDialog.Builder(MainActivity.this).setMessage(text).setPositiveButton("确定", (d,w) -> result.confirm()).setOnCancelListener(d -> result.cancel()).show(); return true;
            }
            @Override public boolean onJsConfirm(WebView view, String url, String text, JsResult result) {
                new AlertDialog.Builder(MainActivity.this).setMessage(text).setPositiveButton("确定", (d,w) -> result.confirm()).setNegativeButton("取消", (d,w) -> result.cancel()).setOnCancelListener(d -> result.cancel()).show(); return true;
            }
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (!local || picker != null || legacyPicker != null) { callback.onReceiveValue(null); return true; } legacyPicker = callback;
                try { startActivityForResult(fileIntent(params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE, String.join(",", params.getAcceptTypes())), PICK_FILE); }
                catch (ActivityNotFoundException error) { legacyPicker = null; callback.onReceiveValue(null); } return true;
            }
        };
    }
    private void openExternal(String value) {
        Uri uri = Uri.parse(value);
        if (!("http".equals(uri.getScheme()) || "https".equals(uri.getScheme())) || uri.getHost() == null || uri.getUserInfo() != null || isLocal(uri)) {
            new AlertDialog.Builder(this).setMessage("此链接类型无法在应用内打开").setPositiveButton("复制链接", (d,w) -> {
                ((ClipboardManager)getSystemService(CLIPBOARD_SERVICE)).setPrimaryClip(ClipData.newPlainText("链接", value)); message("链接已复制");
            }).setNegativeButton("关闭", null).show(); return;
        }
        if (externalDialog != null) externalDialog.dismiss(); Dialog dialog = new Dialog(this, android.R.style.Theme_Material_Light_NoActionBar); externalDialog = dialog;
        LinearLayout root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL);
        root.setOnApplyWindowInsetsListener((v, insets) -> { v.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(), insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom()); return insets; });
        LinearLayout navigation = new LinearLayout(this); TextView address = new TextView(this); address.setText(uri.getHost()); address.setSingleLine(true);
        WebView browser = new WebView(this); externalWeb = browser; configure(browser);
        navigation.addView(button("返回", () -> { if (browser.canGoBack()) browser.goBack(); else dialog.dismiss(); }));
        navigation.addView(address, new LinearLayout.LayoutParams(0, -2, 1)); navigation.addView(button("关闭", dialog::dismiss));
        root.addView(navigation); root.addView(browser, new LinearLayout.LayoutParams(-1, 0, 1)); dialog.setContentView(root); browser.setWebChromeClient(chrome(false));
        browser.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri target = request.getUrl(); boolean allowed = ("http".equals(target.getScheme()) || "https".equals(target.getScheme())) && !isLocal(target) && target.getUserInfo() == null;
                if (!allowed && request.isForMainFrame()) message("此链接类型无法在应用内打开"); return !allowed;
            }
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) { return isLocal(request.getUrl()) ? denied(403, "Forbidden") : null; }
            @Override public void onPageFinished(WebView view, String url) { address.setText(Uri.parse(url).getHost()); }
        });
        browser.setDownloadListener((url, agent, disposition, mime, length) -> message("站外页面仅供浏览；请返回业务页面保存附件"));
        dialog.setOnKeyListener((d,key,event) -> { if (key == KeyEvent.KEYCODE_BACK && event.getAction() == KeyEvent.ACTION_UP) { if (browser.canGoBack()) browser.goBack(); else dialog.dismiss(); return true; } return false; });
        dialog.setOnDismissListener(d -> { browser.destroy(); if (externalDialog == dialog) { externalDialog=null; externalWeb=null; } });
        dialog.show(); dialog.getWindow().setLayout(-1, -1); browser.loadUrl(value);
    }
    @Override public void onBackPressed() {
        if (externalDialog != null) { externalDialog.dismiss(); return; }
        web.evaluateJavascript("Boolean(window.XTYFClient && window.XTYFClient.back && window.XTYFClient.back())", handled -> {
            if (!"true".equals(handled)) new AlertDialog.Builder(this).setMessage("退出巡天御风管理系统？").setPositiveButton("退出", (d,w) -> finish()).setNegativeButton("取消", null).show();
        });
    }
    @Override protected void onPause() {
        super.onPause(); if (web != null) { web.evaluateJavascript("window.dispatchEvent(new CustomEvent('xtyf-foreground',{detail:{active:false}}))", null); web.onPause(); }
        if (externalWeb != null) externalWeb.onPause();
    }
    @Override protected void onResume() {
        super.onResume(); if (web != null) { web.onResume(); web.evaluateJavascript("window.dispatchEvent(new CustomEvent('xtyf-foreground',{detail:{active:true}}))", null); }
        if (externalWeb != null) externalWeb.onResume();
    }
    @Override protected void onDestroy() {
        destroyed = true; mainHandler.removeCallbacksAndMessages(null); closePort(); if (updates != null) updates.retire(); if (client != null) client.cancelAll(); workers.shutdownNow();
        if (legacyPicker != null) legacyPicker.onReceiveValue(null); if (externalDialog != null) externalDialog.dismiss(); if (web != null) web.destroy(); super.onDestroy();
    }
}
