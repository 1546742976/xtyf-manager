package online.skywalker.manager;

import com.sun.net.httpserver.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** Runs the actual Android HTTP implementation against isolated loopback servers. */
public final class NativeHttpClientTest {
    private static int assertions;
    private static void check(boolean valid, String message) { assertions++; if (!valid) throw new AssertionError(message); }
    private static void send(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8); exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream output = exchange.getResponseBody()) { output.write(bytes); }
    }
    private static String get(NativeHttpClient client, String path) throws IOException {
        String[] body = new String[1]; client.request(path, "GET", Collections.emptyMap(), null, UUID.randomUUID().toString(), (status, headers, input) -> {
            ByteArrayOutputStream output = new ByteArrayOutputStream(); NativeHttpClient.copy(input, output); body[0] = output.toString("UTF-8");
        }); return body[0];
    }
    private static void fails(RunnableIO action, String message) throws Exception {
        try { action.run(); throw new AssertionError(message); } catch (IOException expected) { assertions++; }
    }
    interface RunnableIO { void run() throws Exception; }
    public static void main(String[] args) throws Exception {
        check("http://localhost:1234".equals(NativeHttpClient.normalize(" HTTP://LOCALHOST:1234/ ")), "Normalize scheme and host");
        for (String server : Arrays.asList("http://manager.skywalker-rm.online", " HTTP://MANAGER.SKYWALKER-RM.ONLINE/ "))
            check("http://manager.skywalker-rm.online".equals(NativeHttpClient.normalize(server)), "Preserve the built-in HTTP server: " + server);
        check("http://manager.skywalker-rm.online:80".equals(NativeHttpClient.normalize("http://manager.skywalker-rm.online:80/")), "Preserve explicit HTTP port 80");
        for (String unchanged : Arrays.asList("http://manager.skywalker-rm.online:8080", "http://manager.skywalker-rm.online:443", "https://manager.skywalker-rm.online", "https://manager.skywalker-rm.online:8443", "http://example.com", "http://manager.skywalker-rm.online.example.com"))
            check(unchanged.equals(NativeHttpClient.normalize(unchanged)), "Keep the selected server unchanged: " + unchanged);
        NativeHttpClient defaultServer = new NativeHttpClient("http://manager.skywalker-rm.online", "", value -> {});
        check("http://manager.skywalker-rm.online/api/client/bootstrap".equals(defaultServer.resolve("/api/client/bootstrap").toString()), "Default server requests use HTTP");
        fails(() -> defaultServer.resolve("https://manager.skywalker-rm.online/api/client/bootstrap"), "Reject a scheme switch from HTTP to HTTPS");
        NativeHttpClient secureServer = new NativeHttpClient("https://manager.skywalker-rm.online", "", value -> {});
        check("https://manager.skywalker-rm.online/api/client/bootstrap".equals(secureServer.resolve("/api/client/bootstrap").toString()), "Selected HTTPS server requests use HTTPS");
        fails(() -> secureServer.resolve("http://manager.skywalker-rm.online/api/client/bootstrap"), "Reject a scheme switch from HTTPS to HTTP");
        check(NativeHttpClient.probeRedirectAllowed(URI.create("http://example.com/a"), URI.create("https://example.com/b")), "Anonymous probe permits default port HTTPS upgrade");
        check(NativeHttpClient.probeRedirectAllowed(URI.create("http://example.com:8080/a"), URI.create("https://example.com:8080/b")), "Anonymous probe permits same custom port upgrade");
        for (String destination : Arrays.asList("http://evil.example/a", "https://evil.example/a", "https://example.com:8443/a", "https://example.com:80/a", "https://u:p@example.com/a"))
            check(!NativeHttpClient.probeRedirectAllowed(URI.create("http://example.com/a"), URI.create(destination)), "Reject unapproved probe redirect " + destination);
        check(!NativeHttpClient.probeRedirectAllowed(URI.create("https://example.com/a"), URI.create("http://example.com/b")), "Reject HTTPS downgrade");
        check(WebsitePolicy.trusted("https://example.com", "https://example.com/account#avatar"), "Website supports same origin fragments");
        check(WebsitePolicy.blob("https://example.com", "blob:https://example.com/file-id"), "Accept this document origin Blob");
        for (String value : Arrays.asList("https://evil.example/file", "https://example.com:8443/file", "https://user@example.com/file", "https://appassets.androidplatform.net/index.html", "file:///tmp/a", "blob:https://example.com/file"))
            check(!WebsitePolicy.trusted("https://example.com", value), "Reject foreign or privileged website URL " + value);
        check(!WebsitePolicy.blob("https://example.com", "blob:https://evil.example/file"), "Reject foreign Blob origin");
        check(".._secret_.txt".equals(WebsitePolicy.filename("../secret\n.txt")), "Sanitize website download filename");
        WebsitePolicy.size(256L * 1024 * 1024); assertions++;
        fails(() -> WebsitePolicy.size(256L * 1024 * 1024 + 1), "Reject oversized website download");
        fails(() -> WebsitePolicy.size(-1), "Reject invalid Blob size");
        for (String invalid : Arrays.asList("file:///tmp", "http://u:p@example.com", "http://example.com/path", "http://example.com?x", "http://example.com#x", "http://example.com:65536", "not a URL")) fails(() -> NativeHttpClient.normalize(invalid), "Reject " + invalid);
        AtomicInteger outsideHits = new AtomicInteger(), writeHits = new AtomicInteger(); AtomicReference<String> saved = new AtomicReference<>("");
        HttpServer outside = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        outside.createContext("/", exchange -> { outsideHits.incrementAndGet(); send(exchange, 200, "outside"); }); outside.start();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        ExecutorService pool = Executors.newCachedThreadPool(); server.setExecutor(pool);
        CountDownLatch slowStarted = new CountDownLatch(1), slowFinished = new CountDownLatch(1);
        List<String> probeCookies = Collections.synchronizedList(new ArrayList<>());
        AtomicReference<String> probeRedirect = new AtomicReference<>(""); AtomicInteger probeStatus = new AtomicInteger(404);
        AtomicInteger websiteHits = new AtomicInteger(); List<String> websiteCookies = Collections.synchronizedList(new ArrayList<>());
        server.createContext("/", exchange -> { websiteHits.incrementAndGet(); websiteCookies.add(String.valueOf(exchange.getRequestHeaders().getFirst("Cookie"))); send(exchange, 200, "<html>Website</html>"); });
        server.createContext("/api/client/bootstrap", exchange -> {
            probeCookies.add(String.valueOf(exchange.getRequestHeaders().getFirst("Cookie")));
            exchange.getResponseHeaders().add("Set-Cookie", "discovery=must-not-persist; Path=/");
            if (!probeRedirect.get().isEmpty()) { exchange.getResponseHeaders().set("Location", probeRedirect.get()); send(exchange, 302, "redirect"); }
            else send(exchange, probeStatus.get(), "<html>Old server</html>");
        });
        server.createContext("/bootstrap", exchange -> {
            exchange.getResponseHeaders().add("Set-Cookie", "session=logged-in; Path=/; HttpOnly; Max-Age=3600");
            exchange.getResponseHeaders().add("Set-Cookie", "secure-only=hidden; Path=/; Secure");
            exchange.getResponseHeaders().add("Set-Cookie", "other=hidden; Domain=example.org; Path=/");
            exchange.getResponseHeaders().add("X-CSRF-Token", "token-1"); send(exchange, 200, "boot");
        });
        server.createContext("/echo", exchange -> {
            ByteArrayOutputStream body = new ByteArrayOutputStream(); NativeHttpClient.copy(exchange.getRequestBody(), body);
            String cookie = exchange.getRequestHeaders().getFirst("Cookie");
            send(exchange, 200, exchange.getRequestMethod() + "|" + cookie + "|" + exchange.getRequestHeaders().getFirst("X-CSRF-Token") + "|" + exchange.getRequestHeaders().getFirst("X-XTYF-Client-Protocol") + "|" + body.toString("UTF-8"));
        });
        server.createContext("/rotate", exchange -> { exchange.getResponseHeaders().set("X-CSRF-Token", "token-2"); send(exchange, 200, "rotated"); });
        server.createContext("/delete-cookie", exchange -> { exchange.getResponseHeaders().set("Set-Cookie", "session=; Path=/; Max-Age=0"); send(exchange, 200, "deleted"); });
        server.createContext("/same", exchange -> { exchange.getResponseHeaders().set("Location", "/echo"); send(exchange, 302, "redirect"); });
        server.createContext("/outside", exchange -> { exchange.getResponseHeaders().set("Location", "http://127.0.0.1:" + outside.getAddress().getPort() + "/"); send(exchange, 302, "redirect"); });
        server.createContext("/write-redirect", exchange -> { exchange.getResponseHeaders().set("Location", "/write-target"); send(exchange, 307, "redirect"); });
        server.createContext("/write-target", exchange -> { writeHits.incrementAndGet(); send(exchange, 200, "written"); });
        server.createContext("/api/client/logout", exchange -> send(exchange, 200, "logged-out"));
        server.createContext("/unauthorized", exchange -> send(exchange, 401, "unauthorized"));
        server.createContext("/slow", exchange -> {
            exchange.sendResponseHeaders(200, 1024); slowStarted.countDown();
            try { slowFinished.await(10, TimeUnit.SECONDS); exchange.getResponseBody().write(new byte[1024]); } catch (Exception ignored) {} finally { exchange.close(); }
        });
        server.start();
        try {
            String origin = "http://127.0.0.1:" + server.getAddress().getPort();
            NativeHttpClient client = new NativeHttpClient(origin, "", saved::set);
            client.discoverWebsite("manual-open");
            check(websiteHits.get() == 1 && "null".equals(websiteCookies.get(0)), "Manual website opening anonymously discovers root without bootstrap");
            client.discoverWebsite("manual-open-again"); check(websiteHits.get() == 1, "Verified website root is reused without repeated discovery");
            fails(() -> client.resolve("//evil.example/path"), "Block network-path reference");
            fails(() -> client.resolve("https://127.0.0.1/path"), "Block protocol switch");
            fails(() -> client.resolve("/\\evil.example/path"), "Block ambiguous backslash");
            client.request("/bootstrap", "GET", Collections.emptyMap(), null, "bootstrap", (status, headers, input) -> {
                check(status == 200, "Bootstrap status"); check(!headers.containsKey("set-cookie"), "Never return cookie credentials to JS");
            });
            check(!saved.get().isEmpty(), "Persist session");
            check("GET|session=logged-in|token-1|1|".equals(get(client, "/echo")), "Cookie, csrf and protocol sent; foreign/secure cookies omitted");
            client.probe("probe", (status, headers, input) -> check(status == 404, "Expose missing bootstrap to website fallback classifier"));
            check(probeCookies.size() == 2 && "null".equals(probeCookies.get(0)) && "session=logged-in".equals(probeCookies.get(1)), "Discovery is anonymous; final bootstrap restores native authentication");
            check(origin.equals(client.effectiveServer()), "Same origin discovery preserves selected origin");
            check(!probeCookies.get(1).contains("discovery"), "Anonymous discovery cookie was not imported");
            probeStatus.set(503); client.probe("unavailable", (status, headers, input) -> check(status == 503, "Preserve server outage instead of declaring missing protocol"));
            probeStatus.set(200); client.probe("html", (status, headers, input) -> {
                ByteArrayOutputStream output = new ByteArrayOutputStream(); NativeHttpClient.copy(input, output);
                check(status == 200 && output.toString("UTF-8").startsWith("<html>"), "Return successful HTML unchanged for frontend fallback classification");
            });
            probeRedirect.set("http://127.0.0.1:" + outside.getAddress().getPort() + "/");
            fails(() -> client.probe("foreign", (s,h,i) -> {}), "Anonymous probe refuses foreign port redirect");
            check(outsideHits.get() == 0, "Anonymous probe never visits unapproved redirect target");
            probeRedirect.set(""); probeStatus.set(404);
            // Remove the synthetic final bootstrap cookie before legacy session assertions.
            client.clear(); get(client, "/bootstrap");
            NativeHttpClient restored = new NativeHttpClient(origin, saved.get(), saved::set);
            check("GET|session=logged-in|token-1|1|".equals(get(restored, "/echo")), "Restore persistent session across process restart");
            get(restored, "/rotate"); check("token-2".equals(restored.csrf()), "Update rotated CSRF");
            Map<String,String> spoof = new HashMap<>(); spoof.put("Cookie", "attacker"); spoof.put("X-CSRF-Token", "attacker"); spoof.put("X-XTYF-Client-Protocol", "attacker");
            byte[] body = "synthetic upload payload".getBytes(StandardCharsets.UTF_8);
            restored.request("/echo", "POST", spoof, output -> output.write(body), "write", (status, headers, input) -> {
                ByteArrayOutputStream output = new ByteArrayOutputStream(); NativeHttpClient.copy(input, output);
                check("POST|session=logged-in|token-2|1|synthetic upload payload".equals(output.toString("UTF-8")), "Stream body and prevent credential override");
            });
            check(get(restored, "/same").startsWith("GET|session="), "Follow same origin GET redirect");
            fails(() -> get(restored, "/outside"), "Reject external redirects"); check(outsideHits.get() == 0, "Never transmit credentials to another origin");
            restored.request("/write-redirect", "POST", Collections.emptyMap(), null, "no-replay", (status, headers, input) -> check(status == 307, "Expose write redirect without replay"));
            check(writeHits.get() == 0, "Write not repeated");
            check("unauthorized".equals(get(restored, "/unauthorized")), "Read API error body");
            get(restored, "/delete-cookie"); check(get(restored, "/echo").startsWith("GET|null|"), "Honor cookie deletion");
            get(restored, "/bootstrap"); get(restored, "/api/client/logout");
            check("GET|null|null|1|".equals(get(new NativeHttpClient(origin, saved.get(), saved::set), "/echo")), "Logout clears persisted cookie and CSRF");
            Future<?> pending = pool.submit(() -> { try { restored.request("/slow", "GET", Collections.emptyMap(), null, "slow", (s,h,input) -> NativeHttpClient.copy(input, new ByteArrayOutputStream())); throw new AssertionError("Cancellation must fail"); } catch (IOException expected) {} });
            check(slowStarted.await(5, TimeUnit.SECONDS), "Slow request started"); restored.cancel("slow"); slowFinished.countDown(); pending.get(5, TimeUnit.SECONDS); assertions++;
            restored.cancelAll(); fails(() -> get(restored, "/echo"), "Server switch retires queued old requests");
            fails(() -> restored.probe("retired-probe", (s,h,i) -> {}), "Server switch also retires connection probes");
            fails(() -> restored.discoverWebsite("retired-website"), "Server switch also retires manual website discovery");
            NativeHttpClient corrupt = new NativeHttpClient(origin, "not-base64", value -> {}); check("GET|null|null|1|".equals(get(corrupt, "/echo")), "Corrupt local session becomes anonymous");
            System.out.println("ANDROID_HTTP_TESTS_PASSED assertions=" + assertions);
        } finally { slowFinished.countDown(); server.stop(0); outside.stop(0); pool.shutdownNow(); }
    }
}
