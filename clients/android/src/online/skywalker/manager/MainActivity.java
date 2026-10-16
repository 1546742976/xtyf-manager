package online.skywalker.manager;

import android.app.*;
import android.content.*;
import android.graphics.Color;
import android.net.Uri;
import android.os.*;
import android.view.*;
import android.webkit.*;
import android.widget.*;
import java.io.*;
import java.net.URI;
import java.net.URL;
import java.net.HttpURLConnection;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private WebView web;
    private ProgressBar progress;
    private LinearLayout errorPanel;
    private TextView errorText;
    private String server;
    private ValueCallback<Uri[]> upload;
    private String downloadUrl, downloadCookies, downloadAgent;
    private static final int PICK_FILE = 10, SAVE_FILE = 11;

    private int dp(int n) { return Math.round(n * getResources().getDisplayMetrics().density); }
    private void message(String text) { Toast.makeText(this, text, Toast.LENGTH_LONG).show(); }
    private Button button(String title, Runnable action) {
        Button b = new Button(this); b.setText(title); b.setAllCaps(false);
        b.setMinHeight(dp(48)); b.setOnClickListener(v -> action.run()); return b;
    }
    private String normalize(String value) throws Exception {
        URI u = new URI(value.trim()); String path = u.getPath();
        if (!("http".equalsIgnoreCase(u.getScheme()) || "https".equalsIgnoreCase(u.getScheme())) || u.getHost() == null || u.getRawUserInfo() != null || u.getQuery() != null || u.getFragment() != null || !(path == null || path.isEmpty() || "/".equals(path)))
            throw new Exception("请输入完整的网站根地址，不含账号、密码、路径或参数");
        return new URI(u.getScheme().toLowerCase(), null, u.getHost().toLowerCase(), u.getPort(), null, null, null).toString();
    }
    private boolean sameServer(String value) {
        try { URL a = new URL(value), b = new URL(server); return a.getProtocol().equals(b.getProtocol()) && a.getHost().equalsIgnoreCase(b.getHost()) && (a.getPort() < 0 ? a.getDefaultPort() : a.getPort()) == (b.getPort() < 0 ? b.getDefaultPort() : b.getPort()); }
        catch (Exception e) { return false; }
    }
    private void external(String value) {
        Uri uri = Uri.parse(value);
        if (!("http".equals(uri.getScheme()) || "https".equals(uri.getScheme()) || "mailto".equals(uri.getScheme()) || "tel".equals(uri.getScheme()))) return;
        try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); } catch (ActivityNotFoundException e) { message("没有可打开此链接的应用"); }
    }
    private void loadHome() { errorPanel.setVisibility(View.GONE); web.setVisibility(View.VISIBLE); web.loadUrl(server); }
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        server = getPreferences(MODE_PRIVATE).getString("server", BuildConfig.SERVER_URL);
        try { server = normalize(server); } catch (Exception e) { server = BuildConfig.SERVER_URL; }
        LinearLayout root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setBackgroundColor(Color.rgb(245,247,251));
        root.setOnApplyWindowInsetsListener((v, insets) -> { v.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(), insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom()); return insets; });
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal); root.addView(progress, new LinearLayout.LayoutParams(-1, dp(3)));
        errorPanel = new LinearLayout(this); errorPanel.setOrientation(LinearLayout.VERTICAL); errorPanel.setPadding(dp(24),dp(24),dp(24),dp(24));
        errorText = new TextView(this); errorText.setTextSize(18); errorPanel.addView(errorText);
        errorPanel.addView(button("重新连接", () -> loadHome())); errorPanel.addView(button("修改服务器地址", () -> settings()));
        errorPanel.setVisibility(View.GONE); root.addView(errorPanel);
        web = new WebView(this); root.addView(web, new LinearLayout.LayoutParams(-1, 0, 1)); setContentView(root);
        WebSettings cfg = web.getSettings(); cfg.setJavaScriptEnabled(true); cfg.setDomStorageEnabled(true);
        cfg.setAllowFileAccess(false); cfg.setAllowContentAccess(false); cfg.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        cfg.setSupportZoom(true); cfg.setBuiltInZoomControls(true); cfg.setDisplayZoomControls(false); cfg.setUseWideViewPort(true); cfg.setLoadWithOverviewMode(true);
        cfg.setUserAgentString(cfg.getUserAgentString() + " XTYFManager/" + BuildConfig.VERSION);
        CookieManager.getInstance().setAcceptCookie(true); CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString(); if (sameServer(url)) return false;
                if (request.isForMainFrame()) external(url); return true;
            }
            @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) { progress.setVisibility(View.VISIBLE); }
            @Override public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE); CookieManager.getInstance().flush();
                if (!sameServer(url)) return;
                // The site's CSV/ZIP helper uses blob URLs. Navigate to the original
                // authenticated endpoint instead so Android can stream to a user-selected file.
                web.evaluateJavascript("(function(){if(window.__xtyfDownloads)return;window.__xtyfDownloads=true;if(window.BusinessUI){window.BusinessUI.download=function(url){var u=new URL(url,location.href);if(u.origin!==location.origin)return Promise.reject(new Error('下载地址必须属于当前服务器'));location.assign(u.href);return Promise.resolve();};}document.addEventListener('click',function(e){var a=e.target.closest('a[data-download-name],a[download]');if(a&&new URL(a.href).origin===location.origin){e.preventDefault();e.stopImmediatePropagation();location.assign(a.href);}},true);})();", null);
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) { errorText.setText("无法连接服务器\n" + server + "\n" + error.getDescription()); errorPanel.setVisibility(View.VISIBLE); web.setVisibility(View.GONE); progress.setVisibility(View.GONE); }
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onProgressChanged(WebView view, int value) { progress.setProgress(value); }
            @Override public boolean onJsAlert(WebView view, String url, String text, JsResult result) {
                new AlertDialog.Builder(MainActivity.this).setMessage(text).setPositiveButton("确定", (d,w) -> result.confirm()).setOnCancelListener(d -> result.cancel()).show(); return true;
            }
            @Override public boolean onJsConfirm(WebView view, String url, String text, JsResult result) {
                new AlertDialog.Builder(MainActivity.this).setMessage(text).setPositiveButton("确定", (d,w) -> result.confirm()).setNegativeButton("取消", (d,w) -> result.cancel()).setOnCancelListener(d -> result.cancel()).show(); return true;
            }
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (upload != null) upload.onReceiveValue(null); upload = callback;
                Intent pick = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*");
                pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
                try { startActivityForResult(pick, PICK_FILE); } catch (ActivityNotFoundException e) { upload.onReceiveValue(null); upload = null; message("无法打开文件选择器"); }
                return true;
            }
        });
        web.setDownloadListener((url, agent, disposition, mime, length) -> {
            if (!sameServer(url)) { message("只能下载当前服务器的文件"); return; }
            if (downloadUrl != null) { message("请先完成当前文件的保存选择"); return; }
            downloadUrl = url; downloadCookies = CookieManager.getInstance().getCookie(url); downloadAgent = agent;
            Intent save = new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType(mime == null || mime.isEmpty() ? "application/octet-stream" : mime);
            save.putExtra(Intent.EXTRA_TITLE, URLUtil.guessFileName(url, disposition, mime));
            try { startActivityForResult(save, SAVE_FILE); } catch (ActivityNotFoundException e) { downloadUrl = null; message("无法打开文件保存窗口"); }
        });
        if (state == null || web.restoreState(state) == null) loadHome();
    }
    private void settings() {
        LinearLayout panel = new LinearLayout(this); panel.setOrientation(LinearLayout.VERTICAL); panel.setPadding(dp(24),dp(12),dp(24),dp(12));
        TextView label = new TextView(this); label.setText("服务器完整网址（http:// 或 https://）"); panel.addView(label);
        EditText input = new EditText(this); input.setSingleLine(true); input.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_URI); input.setText(server); panel.addView(input);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle("服务器设置").setView(panel).setPositiveButton("保存并连接", null).setNegativeButton("取消", null).create();
        dialog.setOnShowListener(d -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            try { server = normalize(input.getText().toString()); getPreferences(MODE_PRIVATE).edit().putString("server", server).apply(); web.clearHistory(); loadHome(); dialog.dismiss(); }
            catch (Exception e) { input.setError(e.getMessage()); }
        })); dialog.show();
    }
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == PICK_FILE && upload != null) {
            Uri[] uris = null;
            if (result == RESULT_OK && data != null) {
                if (data.getClipData() != null) { ClipData clip = data.getClipData(); uris = new Uri[clip.getItemCount()]; for (int i=0;i<uris.length;i++) uris[i]=clip.getItemAt(i).getUri(); }
                else if (data.getData() != null) uris = new Uri[]{data.getData()};
            }
            upload.onReceiveValue(uris); upload = null;
        }
        if (request == SAVE_FILE) {
            String url = downloadUrl, cookies = downloadCookies, agent = downloadAgent; downloadUrl = null; downloadCookies = null; downloadAgent = null;
            if (result == RESULT_OK && data != null && data.getData() != null && url != null) saveDownload(url, cookies, agent, data.getData());
        }
    }
    private void saveDownload(String url, String cookies, String agent, Uri destination) {
        message("正在下载文件…");
        java.util.concurrent.ExecutorService worker = Executors.newSingleThreadExecutor();
        worker.execute(() -> {
            HttpURLConnection connection = null;
            try {
                connection = (HttpURLConnection)new URL(url).openConnection(); connection.setInstanceFollowRedirects(false); connection.setConnectTimeout(20000); connection.setReadTimeout(180000);
                if (cookies != null) connection.setRequestProperty("Cookie", cookies); if (agent != null) connection.setRequestProperty("User-Agent", agent);
                int status = connection.getResponseCode(); String type = connection.getContentType();
                if (status != 200 || (type != null && (type.contains("text/html") || type.contains("application/json")))) throw new IOException("服务器拒绝下载，请检查登录状态（" + status + "）");
                try (InputStream source = connection.getInputStream(); OutputStream target = getContentResolver().openOutputStream(destination, "wt")) {
                    if (target == null) throw new IOException("无法写入所选文件"); byte[] buffer = new byte[65536]; int count;
                    while ((count = source.read(buffer)) != -1) target.write(buffer, 0, count);
                }
                runOnUiThread(() -> message("文件已保存"));
            } catch (Exception e) { runOnUiThread(() -> message("下载失败：" + e.getMessage())); }
            finally { if (connection != null) connection.disconnect(); worker.shutdown(); }
        });
    }
    private void goBack() { if (web.canGoBack()) web.goBack(); else new AlertDialog.Builder(this).setMessage("退出巡天御风管理系统？").setPositiveButton("退出", (d,w) -> finish()).setNegativeButton("取消", null).show(); }
    @Override public void onBackPressed() { goBack(); }
    @Override protected void onSaveInstanceState(Bundle state) { web.saveState(state); super.onSaveInstanceState(state); }
    @Override protected void onPause() { super.onPause(); web.onPause(); CookieManager.getInstance().flush(); }
    @Override protected void onResume() { super.onResume(); if (web != null) web.onResume(); }
    @Override protected void onDestroy() { if (upload != null) upload.onReceiveValue(null); web.destroy(); super.onDestroy(); }
}
