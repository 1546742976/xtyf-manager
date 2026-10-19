package online.skywalker.manager;

import java.io.*;
import java.net.*;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

/** Authenticated HTTP transport. Never shares CookieManager or credentials with a WebView. */
final class NativeHttpClient {
    interface Persistence { void save(String value); }
    interface Sessions { String load(String origin); void save(String origin, String value); }
    interface Body { void write(OutputStream output) throws IOException; }
    interface Consumer { void accept(int status, Map<String, String> headers, InputStream input) throws IOException; }
    private static final class StoredCookie {
        final HttpCookie cookie; final long receivedAt;
        StoredCookie(HttpCookie cookie, long receivedAt) { this.cookie = cookie; this.receivedAt = receivedAt; }
        boolean expired() { return cookie.getMaxAge() == 0 || cookie.getMaxAge() > 0 && (System.currentTimeMillis() - receivedAt) / 1000 >= cookie.getMaxAge(); }
    }
    private final URI configuredOrigin;
    private volatile URI origin;
    private final Sessions sessions;
    private final Object probeLock = new Object();
    private final List<StoredCookie> cookies = new ArrayList<>();
    private final Map<String, HttpURLConnection> active = new ConcurrentHashMap<>();
    private final Set<String> cancelled = Collections.newSetFromMap(new ConcurrentHashMap<String, Boolean>());
    private String csrf = "";
    private volatile boolean retired;
    private volatile boolean discovered;

    static String normalize(String text) throws IOException {
        try {
            URI value = new URI(text.trim()); String scheme = value.getScheme();
            if (!("http".equalsIgnoreCase(scheme) || "https".equalsIgnoreCase(scheme)) || value.getHost() == null
                    || value.getRawUserInfo() != null || value.getRawQuery() != null || value.getRawFragment() != null
                    || !(value.getPath() == null || value.getPath().isEmpty() || "/".equals(value.getPath()))
                    || value.getPort() > 65535 || value.getPort() == 0 || value.getPort() < -1)
                throw new IOException("请输入完整的网站根地址，不含账号、密码、路径或参数");
            return new URI(scheme.toLowerCase(Locale.ROOT), null, value.getHost().toLowerCase(Locale.ROOT), value.getPort(), null, null, null).toASCIIString();
        } catch (URISyntaxException | NullPointerException error) { throw new IOException("服务器地址无效", error); }
    }
    NativeHttpClient(String server, String saved, Persistence persistence) throws IOException {
        this(server, singleOriginSessions(normalize(server), saved, persistence));
    }
    private static Sessions singleOriginSessions(String original, String saved, Persistence persistence) {
        return new Sessions() {
            public String load(String origin) { return original.equals(origin) ? saved : ""; }
            public void save(String origin, String value) { if (original.equals(origin)) persistence.save(value); }
        };
    }
    NativeHttpClient(String server, Sessions sessions) throws IOException {
        this.origin = URI.create(normalize(server)); this.configuredOrigin = origin; this.sessions = sessions; restore(sessions.load(origin.toString()));
    }
    String effectiveServer() { return origin.toString(); }
    void discoverWebsite(String requestId) throws IOException {
        synchronized (probeLock) {
            if (discovered) { checkCancelled(requestId); return; }
            try {
                URI reached = request(configuredOrigin.resolve("/"), "GET", Collections.emptyMap(), null,
                    requestId, (status, headers, input) -> {}, true);
                checkCancelled(requestId); adoptOrigin(reached);
            } finally { cancelled.remove(requestId); }
        }
    }
    private synchronized void adoptOrigin(URI reached) throws IOException {
        URI actual = URI.create(normalize(reached.getScheme() + "://" + reached.getRawAuthority()));
        if (!origin.equals(actual)) { origin = actual; cookies.clear(); csrf = ""; restore(sessions.load(actual.toString())); }
        discovered = true;
    }
    private static int port(URI value) { return value.getPort() >= 0 ? value.getPort() : "https".equalsIgnoreCase(value.getScheme()) ? 443 : 80; }
    private boolean sameOrigin(URI value) {
        return sameOrigin(origin, value);
    }
    static boolean sameOrigin(URI origin, URI value) {
        return value.getHost() != null && origin.getScheme().equalsIgnoreCase(value.getScheme()) && origin.getHost().equalsIgnoreCase(value.getHost()) && port(origin) == port(value)
                && value.getRawUserInfo() == null && value.getFragment() == null;
    }
    static boolean probeRedirectAllowed(URI from, URI to) {
        return sameOrigin(from, to) || to.getHost() != null && from.getHost().equalsIgnoreCase(to.getHost())
            && "http".equalsIgnoreCase(from.getScheme()) && "https".equalsIgnoreCase(to.getScheme())
            && (port(from) == 80 && port(to) == 443 || port(from) != 80 && port(from) == port(to))
            && to.getRawUserInfo() == null && to.getFragment() == null;
    }
    URI resolve(String path) throws IOException {
        try {
            if (path.indexOf('\\') >= 0 || path.indexOf('\r') >= 0 || path.indexOf('\n') >= 0) throw new IOException("请求地址无效");
            URI value = origin.resolve(new URI(path)); if (!sameOrigin(value)) throw new IOException("请求只能访问当前服务器"); return value;
        } catch (IllegalArgumentException | URISyntaxException error) { throw new IOException("请求地址无效", error); }
    }
    synchronized void clear() { cookies.clear(); csrf = ""; persist(); }
    void cancel(String id) { cancelled.add(id); HttpURLConnection connection = active.get(id); if (connection != null) connection.disconnect(); }
    void cancelAll() { retired = true; for (String id : active.keySet()) cancel(id); }
    private void checkCancelled(String id) throws InterruptedIOException { if (retired || cancelled.contains(id) || Thread.currentThread().isInterrupted()) throw new InterruptedIOException("请求已取消"); }
    synchronized String csrf() { return csrf; }
    private synchronized String[] credentials(URI target) throws IOException {
        if (!sameOrigin(target)) throw new IOException("服务器连接地址已更新，请重试");
        return new String[]{cookieHeader(target), csrf};
    }
    private synchronized String cookieHeader(URI target) {
        StringBuilder result = new StringBuilder(); cookies.removeIf(StoredCookie::expired);
        String targetPath = target.getPath().isEmpty() ? "/" : target.getPath();
        for (StoredCookie stored : cookies) {
            HttpCookie cookie = stored.cookie; String path = cookie.getPath() == null ? "/" : cookie.getPath();
            boolean matches = targetPath.equals(path) || targetPath.startsWith(path) && (path.endsWith("/") || targetPath.charAt(path.length()) == '/');
            if (matches && (!cookie.getSecure() || "https".equalsIgnoreCase(target.getScheme()))) {
                if (result.length() > 0) result.append("; "); result.append(cookie.getName()).append('=').append(cookie.getValue());
            }
        }
        return result.toString();
    }
    private synchronized void receive(URI target, Map<String, List<String>> headers) {
        if (!sameOrigin(target) || retired) return;
        for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
            if (entry.getKey() == null) continue;
            if ("X-CSRF-Token".equalsIgnoreCase(entry.getKey()) && !entry.getValue().isEmpty()) csrf = entry.getValue().get(0);
            if (!"Set-Cookie".equalsIgnoreCase(entry.getKey())) continue;
            for (String line : entry.getValue()) try {
                for (HttpCookie cookie : HttpCookie.parse(line)) {
                    String domain = cookie.getDomain();
                    if (domain != null && !(target.getHost().equalsIgnoreCase(domain) || HttpCookie.domainMatches(domain, target.getHost()))) continue;
                    if (cookie.getPath() == null) { String path = target.getPath(); cookie.setPath(path.lastIndexOf('/') > 0 ? path.substring(0, path.lastIndexOf('/')) : "/"); }
                    cookies.removeIf(old -> old.cookie.getName().equals(cookie.getName()) && Objects.equals(old.cookie.getPath(), cookie.getPath()));
                    if (cookie.getMaxAge() != 0) cookies.add(new StoredCookie(cookie, System.currentTimeMillis()));
                }
            } catch (IllegalArgumentException ignored) { /* Ignore malformed cookies. */ }
        }
        persist();
    }
    private synchronized void persist() {
        try {
            ByteArrayOutputStream bytes = new ByteArrayOutputStream(); DataOutputStream data = new DataOutputStream(bytes);
            data.writeInt(1); data.writeUTF(csrf); data.writeInt(cookies.size());
            for (StoredCookie stored : cookies) {
                HttpCookie cookie = stored.cookie;
                data.writeUTF(cookie.getName()); data.writeUTF(cookie.getValue()); data.writeUTF(cookie.getPath());
                data.writeLong(cookie.getMaxAge()); data.writeLong(stored.receivedAt); data.writeBoolean(cookie.getSecure());
            }
            data.close(); sessions.save(origin.toString(), Base64.getEncoder().encodeToString(bytes.toByteArray()));
        } catch (IOException ignored) { /* An invalid cookie must not break the completed request. */ }
    }
    private synchronized void restore(String saved) {
        if (saved == null || saved.isEmpty()) return;
        try (DataInputStream data = new DataInputStream(new ByteArrayInputStream(Base64.getDecoder().decode(saved)))) {
            if (data.readInt() != 1) return;
            csrf = data.readUTF(); int count = data.readInt(); if (count < 0 || count > 1000) throw new IOException("Invalid cookie store");
            for (int index = 0; index < count; index++) {
                HttpCookie cookie = new HttpCookie(data.readUTF(), data.readUTF()); cookie.setPath(data.readUTF()); cookie.setMaxAge(data.readLong());
                long received = data.readLong(); cookie.setSecure(data.readBoolean()); StoredCookie stored = new StoredCookie(cookie, received); if (!stored.expired()) cookies.add(stored);
            }
        } catch (IOException | IllegalArgumentException error) { cookies.clear(); csrf = ""; }
    }
    void request(String path, String method, Map<String, String> headers, Body body, String requestId, Consumer consume) throws IOException {
        request(resolve(path), method, headers, body, requestId, consume, false);
    }
    void probe(String requestId, Consumer consume) throws IOException {
        synchronized (probeLock) {
            try {
                URI reached = request(configuredOrigin.resolve("/api/client/bootstrap"), "GET", Collections.emptyMap(), null,
                    requestId, (status, headers, input) -> {}, true);
                checkCancelled(requestId);
                adoptOrigin(reached);
                request(reached, "GET", Collections.emptyMap(), null, requestId, consume, false);
            } finally { cancelled.remove(requestId); }
        }
    }
    private URI request(URI target, String method, Map<String, String> headers, Body body, String requestId, Consumer consume, boolean probe) throws IOException {
        String verb = method.toUpperCase(Locale.ROOT);
        if (!Arrays.asList("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS").contains(verb)) throw new IOException("不支持的请求方法");
        try {
            for (int redirects = 0; redirects < 6; redirects++) {
                checkCancelled(requestId); HttpURLConnection connection = (HttpURLConnection) target.toURL().openConnection(); active.put(requestId, connection);
                try {
                    connection.setConnectTimeout(20000); connection.setReadTimeout(180000); connection.setInstanceFollowRedirects(false); connection.setUseCaches(false); connection.setRequestMethod(verb);
                    connection.setRequestProperty("User-Agent", "XTYFManager-Native/2 Android"); connection.setRequestProperty("Accept", "application/json");
                    for (Map.Entry<String, String> entry : headers.entrySet()) {
                        String name = entry.getKey();
                        if (!Arrays.asList("cookie", "host", "origin", "referer", "content-length", "connection", "transfer-encoding", "x-csrf-token", "authorization", "sec-fetch-site", "x-xtyf-client-protocol").contains(name.toLowerCase(Locale.ROOT))) connection.setRequestProperty(name, entry.getValue());
                    }
                    connection.setRequestProperty("X-XTYF-Client-Protocol", "1");
                    String[] credentials = probe ? new String[]{"", ""} : credentials(target);
                    if (!credentials[0].isEmpty()) connection.setRequestProperty("Cookie", credentials[0]);
                    if (!credentials[1].isEmpty()) connection.setRequestProperty("X-CSRF-Token", credentials[1]);
                    if (body != null) {
                        connection.setDoOutput(true); connection.setChunkedStreamingMode(65536);
                        try (OutputStream output = connection.getOutputStream()) { checkCancelled(requestId); body.write(output); }
                    }
                    checkCancelled(requestId); int status = connection.getResponseCode(); if (!probe) receive(target, connection.getHeaderFields());
                    String location = connection.getHeaderField("Location");
                    if (status >= 300 && status < 400 && location != null) {
                        URI redirected; try { redirected = target.resolve(location); } catch (IllegalArgumentException e) { throw new IOException("重定向地址无效", e); }
                        if (!(probe ? probeRedirectAllowed(target, redirected) : sameOrigin(redirected))) throw new IOException("服务器重定向到其他地址，请在设置中确认服务器地址");
                        // Never replay writes automatically, including 307/308 responses.
                        if (("GET".equals(verb) || "HEAD".equals(verb)) && Arrays.asList(301, 302, 303, 307, 308).contains(status)) { target = redirected; continue; }
                    }
                    Map<String, String> responseHeaders = new LinkedHashMap<>();
                    for (Map.Entry<String, List<String>> entry : connection.getHeaderFields().entrySet()) {
                        if (entry.getKey() != null && !"Set-Cookie".equalsIgnoreCase(entry.getKey())) responseHeaders.put(entry.getKey().toLowerCase(Locale.ROOT), String.join(", ", entry.getValue()));
                    }
                    InputStream source = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
                    try (InputStream input = source == null ? new ByteArrayInputStream(new byte[0]) : source) { consume.accept(status, responseHeaders, input); }
                    checkCancelled(requestId);
                    if (!probe && "/api/client/logout".equals(target.getPath()) && status >= 200 && status < 300) clear(); return target;
                } finally { active.remove(requestId, connection); connection.disconnect(); }
            }
            throw new IOException("服务器重定向次数过多");
        } finally { if (!probe) cancelled.remove(requestId); }
    }
    static void copy(InputStream source, OutputStream target) throws IOException {
        byte[] buffer = new byte[65536]; int count;
        while ((count = source.read(buffer)) != -1) { if (Thread.currentThread().isInterrupted()) throw new InterruptedIOException("请求已取消"); target.write(buffer, 0, count); }
    }
}
