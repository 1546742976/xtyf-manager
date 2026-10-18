package online.skywalker.manager;

import org.json.*;
import java.io.*;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.security.interfaces.RSAPublicKey;
import java.security.spec.X509EncodedKeySpec;
import java.util.*;
import java.util.zip.*;

/** Private, per-server signed resource storage. No Android/UI dependency, so the real code is testable on a JVM. */
final class UiUpdateStore {
    static final int META_LIMIT = 256 * 1024, ZIP_LIMIT = 128 * 1024 * 1024;
    static final long UNPACKED_LIMIT = 256L * 1024 * 1024;
    private static final Set<String> CAPABILITIES = new HashSet<>(Arrays.asList("ui_updates", "image_preview"));
    private static final List<String> REQUIRED = Arrays.asList("index.html", "shell.js", "bridge.js", "page-runtime.js", "templates.js");
    private final File root, bundles, stateFile;
    private final PublicKey key;
    private final String clientVersion;
    private final NativeHttpClient transport;
    private JSONObject state;
    private String persisted;
    private String status = "current", message = "", active = "", activeVersion;
    private JSONObject installer;
    private boolean retired, checking, disabled;

    UiUpdateStore(File parent, String server, String clientVersion, String pem, NativeHttpClient transport) throws IOException {
        this.clientVersion = clientVersion; this.activeVersion = clientVersion; this.transport = transport;
        root = new File(parent, hash(NativeHttpClient.normalize(server).getBytes(StandardCharsets.UTF_8)));
        bundles = new File(root, "bundles"); stateFile = new File(root, "state.json");
        PublicKey loadedKey = null;
        try {
            if (!bundles.isDirectory() && !bundles.mkdirs()) throw new IOException("无法创建页面更新目录");
            byte[] bytes = Base64.getDecoder().decode(pem.replace("-----BEGIN PUBLIC KEY-----", "").replace("-----END PUBLIC KEY-----", "").replaceAll("\\s", ""));
            loadedKey = KeyFactory.getInstance("RSA").generatePublic(new X509EncodedKeySpec(bytes));
            if (!(loadedKey instanceof RSAPublicKey) || ((RSAPublicKey)loadedKey).getModulus().bitLength() != 3072) throw new IOException("页面更新公钥必须为 RSA-3072");
            state = stateFile.exists() ? json(read(new FileInputStream(stateFile), META_LIMIT)) : new JSONObject();
            validateState();
            installer = state.optJSONObject("installer");
        } catch (GeneralSecurityException | IllegalArgumentException | IOException | JSONException error) {
            // Never overwrite a damaged anti-replay ledger or let it reset the chosen server.
            // The bundled UI/settings stay available, while this server's updater fails closed.
            disabled = true; state = new JSONObject(); status = "failed";
            message = "本地更新状态不可用，已保留原数据并使用内置页面：" + error.getMessage();
        }
        key = loadedKey; persisted = state.toString();
    }

    private void validateState() throws JSONException, IOException {
        for (String name : Arrays.asList("current", "previous", "pending", "trial", "trialFallback")) if (state.has(name)) {
            Object value = state.get(name);
            if (!(value instanceof String) || !((String)value).isEmpty() && !((String)value).matches("[a-f0-9]{64}")) throw new IOException("页面更新状态标识损坏");
        }
        if (state.has("highest") && integer(state, "highest", 0, 9007199254740991L) > 0) digest(state, "payloadHash");
        Object values = state.opt("quarantined");
        if (values != null && !(values instanceof String) && !(values instanceof JSONArray)) throw new IOException("失败版本记录损坏");
        JSONArray blocked = quarantined();
        for (int i = 0; i < blocked.length(); i++) {
            Object value = blocked.get(i); if (!(value instanceof String) || !((String)value).matches("[a-f0-9]{64}")) throw new IOException("失败版本记录损坏");
        }
    }
    private JSONArray quarantined() {
        Object values = state.opt("quarantined"); if (values instanceof JSONArray) return (JSONArray)values;
        JSONArray result = new JSONArray(); if (values instanceof String && !((String)values).isEmpty()) result.put(values); return result;
    }
    private boolean isQuarantined(String id) {
        JSONArray values = quarantined(); for (int i = 0; i < values.length(); i++) if (id.equals(values.optString(i))) return true; return false;
    }
    private void quarantine(String id) throws IOException {
        if (!id.isEmpty() && !isQuarantined(id)) { JSONArray values = quarantined(); values.put(id); field("quarantined", values); }
    }
    private void allowRetry(String id) throws IOException {
        JSONArray values = quarantined(), remaining = new JSONArray();
        for (int i = 0; i < values.length(); i++) if (!id.equals(values.optString(i))) remaining.put(values.optString(i)); field("quarantined", remaining);
    }
    private String launchable(String id) throws IOException { return isQuarantined(id) ? "" : usable(id); }

    synchronized void retire() { retired = true; }
    private synchronized void alive() throws IOException { if (retired || Thread.currentThread().isInterrupted()) throw new InterruptedIOException("服务器已切换，更新已取消"); }
    private static JSONObject json(byte[] data) throws JSONException { return new JSONObject(new String(data, StandardCharsets.UTF_8)); }
    static byte[] read(InputStream input, long limit) throws IOException {
        try (InputStream source = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            copyLimited(source, output, limit); return output.toByteArray();
        }
    }
    private static long copyLimited(InputStream input, OutputStream output, long limit) throws IOException {
        byte[] buffer = new byte[65536]; long count = 0; int n;
        while ((n = input.read(buffer)) != -1) {
            if (Thread.currentThread().isInterrupted()) throw new InterruptedIOException("操作已取消");
            count += n; if (count > limit) throw new IOException("文件超过允许大小"); output.write(buffer, 0, n);
        }
        return count;
    }
    static String hash(byte[] bytes) throws IOException {
        try { return hex(MessageDigest.getInstance("SHA-256").digest(bytes)); } catch (GeneralSecurityException error) { throw new IOException(error); }
    }
    static String hashFile(File file) throws IOException {
        try (InputStream input = new FileInputStream(file)) {
            MessageDigest digest = MessageDigest.getInstance("SHA-256"); byte[] buffer = new byte[65536]; int n;
            while ((n = input.read(buffer)) != -1) digest.update(buffer, 0, n);
            return hex(digest.digest());
        } catch (GeneralSecurityException error) { throw new IOException(error); }
    }
    private static String hex(byte[] bytes) { StringBuilder result = new StringBuilder(); for (byte b : bytes) result.append(String.format(Locale.ROOT, "%02x", b & 255)); return result.toString(); }
    private static String digest(JSONObject object, String name) throws JSONException, IOException {
        Object value = object.get(name); if (!(value instanceof String) || !((String)value).matches("[a-f0-9]{64}")) throw new IOException("摘要字段无效：" + name); return (String)value;
    }
    private static long integer(JSONObject object, String name, long min, long max) throws JSONException, IOException {
        Object value = object.get(name); if (!(value instanceof Number)) throw new IOException("数值字段无效：" + name);
        double number = ((Number)value).doubleValue(); long result = ((Number)value).longValue();
        if (!Double.isFinite(number) || number != result || result < min || result > max) throw new IOException("数值字段越界：" + name); return result;
    }
    static String assetPath(String path) throws IOException {
        if (path.isEmpty() || path.startsWith("/") || path.indexOf('\\') >= 0 || path.indexOf(':') >= 0 || path.indexOf('\0') >= 0) throw new IOException("资源路径无效");
        for (String part : path.split("/", -1)) if (part.isEmpty() || ".".equals(part) || "..".equals(part)) throw new IOException("资源路径越界");
        return path;
    }
    static String serverPath(String path) throws IOException {
        try {
            URI value = URI.create(path);
            if (!path.startsWith("/") || path.startsWith("//") || path.indexOf('\\') >= 0 || value.getRawAuthority() != null || value.getFragment() != null || value.getScheme() != null)
                throw new IOException("更新链接必须属于当前服务器");
            assetPath(value.getPath().substring(1)); return path;
        } catch (IllegalArgumentException error) { throw new IOException("更新链接无效", error); }
    }
    private static int compareVersions(String actual, String minimum) throws IOException {
        if (!actual.matches("[0-9]+\\.[0-9]+\\.[0-9]+") || !minimum.matches("[0-9]+\\.[0-9]+\\.[0-9]+")) throw new IOException("客户端版本格式无效");
        String[] a = actual.split("\\."), b = minimum.split("\\.");
        try { for (int i = 0; i < 3; i++) { int diff = Long.compare(Long.parseLong(a[i]), Long.parseLong(b[i])); if (diff != 0) return diff; } }
        catch (NumberFormatException error) { throw new IOException("客户端版本格式无效", error); }
        return 0;
    }
    private void save() throws IOException {
        if (disabled) throw new IOException(message);
        File temporary = new File(root, "state-" + UUID.randomUUID() + ".tmp");
        try {
            try (FileOutputStream output = new FileOutputStream(temporary)) { output.write(state.toString().getBytes(StandardCharsets.UTF_8)); output.getFD().sync(); }
            Files.move(temporary.toPath(), stateFile.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            persisted = state.toString();
        } catch (IOException error) {
            try { state = new JSONObject(persisted); } catch (JSONException impossible) { throw new IOException(impossible); }
            throw error;
        } finally { if (temporary.exists()) temporary.delete(); }
    }
    private File directory(String id) throws IOException { if (!id.matches("[a-f0-9]{64}")) throw new IOException("资源标识无效"); return new File(bundles, id); }
    private String usable(String id) throws IOException {
        if (id.isEmpty()) return "";
        try {
            File folder = directory(id); byte[] bytes = read(new FileInputStream(new File(folder, "asset-manifest.json")), META_LIMIT);
            if (!id.equals(hash(bytes))) return "";
            JSONObject manifest = json(bytes), files = manifest.getJSONObject("files"), sizes = manifest.getJSONObject("sizes");
            for (Iterator<String> it = files.keys(); it.hasNext();) {
                String name = assetPath(it.next()); File file = new File(folder, name);
                if (!file.isFile() || file.length() != integer(sizes, name, 0, UNPACKED_LIMIT) || !hashFile(file).equals(digest(files, name))) return "";
            }
            return id;
        } catch (IOException | JSONException error) { return ""; }
    }
    private void field(String name, Object value) throws IOException { try { state.put(name, value); } catch (JSONException error) { throw new IOException(error); } }
    private String version(String id) {
        if (id.isEmpty()) return clientVersion;
        JSONObject versions = state.optJSONObject("versions"); return versions == null ? id : versions.optString(id, id);
    }
    synchronized JSONObject status() throws JSONException {
        return new JSONObject().put("status", status).put("currentVersion", activeVersion).put("pendingVersion", state.optString("pending", "").isEmpty() ? JSONObject.NULL : version(state.optString("pending")))
            .put("message", message).put("installer", installer == null ? JSONObject.NULL : new JSONObject(installer.toString()));
    }
    synchronized void validateInstaller(String path, String expectedSha256) throws IOException {
        alive();
        if (disabled) throw new IOException(message);
        if (!expectedSha256.matches("[a-f0-9]{64}") || installer == null || !path.equals(installer.optString("url")) || !expectedSha256.equals(installer.optString("sha256")))
            throw new IOException("安装包链接或摘要未通过当前发布清单验证");
    }
    /** Called once per new trusted WebView. Returned root is pinned until that view is destroyed. */
    synchronized File startView(boolean activatePending) throws IOException {
        alive();
        if (disabled) return null;
        if (!state.optString("trial", "").isEmpty()) failTrial();
        String savedCurrent = state.optString("current", "");
        if (!savedCurrent.isEmpty() && launchable(savedCurrent).isEmpty()) {
            quarantine(savedCurrent); field("current", launchable(state.optString("previous", ""))); field("previous", "");
        }
        String candidate = activatePending ? launchable(state.optString("pending", "")) : "";
        if (activatePending && candidate.isEmpty() && !state.optString("pending", "").isEmpty()) {
            quarantine(state.optString("pending")); field("pending", "");
        }
        if (!candidate.isEmpty()) { field("pending", ""); field("trial", candidate); field("trialFallback", launchable(state.optString("current", ""))); }
        else { candidate = launchable(state.optString("current", "")); if (!candidate.isEmpty()) { field("trial", candidate); field("trialFallback", launchable(state.optString("previous", ""))); } }
        active = candidate; activeVersion = version(active); save();
        return active.isEmpty() ? null : directory(active);
    }
    synchronized void ready() throws IOException {
        alive(); if (disabled) return; String trial = state.optString("trial", "");
        if (!trial.isEmpty() && trial.equals(active)) {
            String old = state.optString("current", ""); if (!trial.equals(old)) field("previous", old);
            field("current", trial); field("trial", ""); field("trialFallback", ""); save();
        }
        status = state.optString("pending", "").isEmpty() ? "current" : "pending"; message = "";
        try { cleanup(); } catch (IOException ignored) { /* Old files can be retried next start; a healthy view must not roll back. */ }
    }
    /** An unconfirmed view failed: quarantine it and return exactly one prior success, then bundled UI. */
    synchronized File failView() throws IOException {
        alive(); if (disabled) return null; failTrial(); active = launchable(state.optString("current", ""));
        if (!active.isEmpty()) { field("trial", active); field("trialFallback", ""); }
        activeVersion = version(active); save(); status = "failed"; message = "新版页面启动失败，已回退";
        return active.isEmpty() ? null : directory(active);
    }
    private void failTrial() throws IOException {
        String trial = state.optString("trial", ""); if (trial.isEmpty()) return;
        quarantine(trial); String fallback = launchable(state.optString("trialFallback", ""));
        if (trial.equals(fallback)) fallback = "";
        field("current", fallback); field("previous", ""); field("trial", ""); field("trialFallback", ""); save();
    }
    private void cleanup() throws IOException {
        Set<String> keep = new HashSet<>(Arrays.asList(active, state.optString("current", ""), state.optString("previous", ""), state.optString("pending", "")));
        File[] children = bundles.listFiles(); if (children != null) for (File child : children) if (!keep.contains(child.getName())) delete(child);
    }
    private static void delete(File file) throws IOException {
        if (!Files.isSymbolicLink(file.toPath()) && file.isDirectory()) { File[] children = file.listFiles(); if (children != null) for (File child : children) delete(child); }
        Files.deleteIfExists(file.toPath());
    }
    JSONObject check(boolean retry) throws JSONException {
        synchronized (this) { if (disabled || checking) return status(); checking = true; }
        File stage = null, zip = null;
        try {
            alive(); final byte[][] envelopeBytes = new byte[1][];
            transport.request("/api/client/updates", "GET", Collections.emptyMap(), null, "ui-update-manifest", (code, headers, input) -> {
                if (code != 200) throw new IOException("无法读取页面更新（" + code + "）"); envelopeBytes[0] = read(input, META_LIMIT);
            });
            JSONObject envelope = json(envelopeBytes[0]);
            if (!Boolean.TRUE.equals(envelope.get("ok"))) throw new IOException("更新清单无效");
            if (Boolean.FALSE.equals(envelope.get("available"))) { synchronized (this) { alive(); status = state.optString("pending", "").isEmpty() ? "unavailable" : "pending"; message = "服务器暂无可用页面更新"; return status(); } }
            if (!Boolean.TRUE.equals(envelope.get("available"))) throw new IOException("更新清单无效");
            byte[] payload = Base64.getDecoder().decode(envelope.getString("payload"));
            byte[] signature = Base64.getDecoder().decode(envelope.getString("signature"));
            if (payload.length > META_LIMIT || signature.length != 384) throw new IOException("更新签名无效");
            Signature verifier = Signature.getInstance("SHA256withRSA"); verifier.initVerify(key); verifier.update(payload);
            if (!verifier.verify(signature)) throw new IOException("页面更新签名校验失败");
            JSONObject release = json(payload); integer(release, "format", 1, 1);
            long sequence = integer(release, "sequence", 1, 9007199254740991L); String payloadHash = hash(payload);
            JSONObject bundle = release.getJSONObject("bundle"), installers = release.getJSONObject("installers");
            String id = digest(bundle, "id"), expectedHash = digest(bundle, "sha256"), path = serverPath(bundle.getString("url"));
            long size = integer(bundle, "size", 1, ZIP_LIMIT), unpacked = integer(bundle, "unpackedSize", 1, UNPACKED_LIMIT);
            int count = (int)integer(bundle, "fileCount", 1, 4096);
            long min = integer(bundle, "protocolMin", 1, 9007199254740991L), max = integer(bundle, "protocolMax", min, 9007199254740991L), bridge = integer(bundle, "bridgeMin", 1, 9007199254740991L);
            boolean compatible = min <= 1 && max >= 1 && bridge <= 2 && compareVersions(clientVersion, bundle.getString("minClientVersion")) >= 0;
            JSONArray caps = bundle.getJSONArray("capabilities"); for (int i = 0; i < caps.length(); i++) if (!CAPABILITIES.contains(caps.getString(i))) compatible = false;
            JSONObject platformInstaller = installers.optJSONObject("android");
            if (platformInstaller != null) { serverPath(platformInstaller.getString("url")); digest(platformInstaller, "sha256"); compareVersions(platformInstaller.getString("version"), "0.0.0"); }
            synchronized (this) {
                alive(); long highest = state.optLong("highest", 0);
                if (sequence < highest || sequence == highest && !payloadHash.equals(state.optString("payloadHash", ""))) throw new IOException("拒绝过期或同序号被修改的更新清单");
                field("highest", sequence); field("payloadHash", payloadHash); field("installer", platformInstaller == null ? JSONObject.NULL : platformInstaller);
                if (!compatible || !id.equals(state.optString("pending", ""))) field("pending", "");
                save(); installer = platformInstaller;
                if (!compatible) { status = "incompatible"; message = "新页面需要升级客户端原生能力或协议"; return status(); }
                if (!retry && isQuarantined(id)) { status = "failed"; message = "此页面版本启动失败；可手动重试或等待新发布"; return status(); }
                if (id.equals(state.optString("pending", "")) || id.equals(active)) { status = id.equals(active) ? "current" : "pending"; message = ""; return status(); }
            }
            zip = new File(root, "download-" + UUID.randomUUID() + ".zip"); final File destination = zip;
            transport.request(path, "GET", Collections.emptyMap(), null, "ui-update-archive", (code, headers, input) -> {
                if (code != 200) throw new IOException("页面资源下载失败（" + code + "）");
                try (FileOutputStream output = new FileOutputStream(destination)) { if (copyLimited(input, output, size) != size) throw new IOException("页面资源下载不完整"); output.getFD().sync(); }
            });
            alive(); if (!hashFile(zip).equals(expectedHash)) throw new IOException("页面资源摘要校验失败");
            stage = new File(root, "stage-" + UUID.randomUUID()); unpack(zip, stage, id, unpacked, count);
            synchronized (this) {
                alive(); File published = directory(id);
                if (published.exists() && usable(id).isEmpty()) {
                    if (id.equals(active) || id.equals(state.optString("current", "")) || id.equals(state.optString("previous", ""))) throw new IOException("正在使用的资源已损坏，请重新启动后重试");
                    delete(published);
                }
                if (!published.exists()) Files.move(stage.toPath(), published.toPath(), StandardCopyOption.ATOMIC_MOVE);
                JSONObject versions = state.optJSONObject("versions"); if (versions == null) versions = new JSONObject(); versions.put(id, bundle.getString("version")); field("versions", versions);
                field("pending", id); if (retry) allowRetry(id); save(); status = "pending"; message = "页面已下载，将在下次启动生效"; return status();
            }
        } catch (Exception error) {
            synchronized (this) { status = "failed"; message = error.getMessage() == null ? "页面更新失败" : error.getMessage(); return status(); }
        } finally {
            if (stage != null) try { delete(stage); } catch (IOException ignored) { /* Unpublished temporary data is never loaded. */ }
            if (zip != null) zip.delete(); synchronized (this) { checking = false; }
        }
    }

    /** The central directory is inspected because ZipInputStream does not expose Unix symlink attributes. */
    private static Set<String> central(File file, int count) throws IOException {
        try (RandomAccessFile input = new RandomAccessFile(file, "r")) {
            long end = -1;
            for (long p = input.length() - 22, first = Math.max(0, p - 65535); p >= first; p--) {
                input.seek(p); if (u32(input) == 0x06054b50L) { input.seek(p + 20); if (p + 22 + u16(input) == input.length()) { end = p; break; } }
            }
            if (end < 0) throw new IOException("ZIP 目录缺失"); input.seek(end + 4);
            if (u16(input) != 0 || u16(input) != 0 || u16(input) != count || u16(input) != count) throw new IOException("ZIP 文件数量或分卷无效");
            long length = u32(input), offset = u32(input); if (offset + length != end) throw new IOException("ZIP 目录范围无效");
            input.seek(offset); Set<String> names = new HashSet<>();
            for (int i = 0; i < count; i++) {
                long start = input.getFilePointer(); if (u32(input) != 0x02014b50L) throw new IOException("ZIP 目录无效");
                input.seek(start + 8); int flags = u16(input), method = u16(input);
                if ((flags & 1) != 0 || !(method == 0 || method == 8)) throw new IOException("ZIP 压缩方式无效");
                input.seek(start + 28); int nameLength = u16(input), extra = u16(input), comment = u16(input);
                input.seek(start + 38); long attributes = u32(input); int type = ((int)(attributes >>> 16)) & 0170000;
                if (type != 0 && type != 0100000 || (attributes & 0x10) != 0) throw new IOException("资源包不能包含链接或目录");
                input.seek(start + 46); byte[] name = new byte[nameLength]; input.readFully(name);
                String path = new String(name, StandardCharsets.UTF_8); if (!Arrays.equals(name, path.getBytes(StandardCharsets.UTF_8)) || !names.add(assetPath(path))) throw new IOException("ZIP 文件路径重复或无效");
                input.seek(start + 46 + nameLength + extra + comment);
            }
            if (input.getFilePointer() != end) throw new IOException("ZIP 目录尾部无效"); return names;
        }
    }
    private static int u16(RandomAccessFile file) throws IOException { int lo = file.readUnsignedByte(); return lo | file.readUnsignedByte() << 8; }
    private static long u32(RandomAccessFile file) throws IOException { return (u16(file) | (long)u16(file) << 16); }
    static void unpack(File zip, File stage, String id, long declaredSize, int declaredCount) throws IOException, JSONException {
        if (zip.length() > ZIP_LIMIT || declaredCount < 1 || declaredCount > 4096 || declaredSize < 1 || declaredSize > UNPACKED_LIMIT) throw new IOException("资源包超过限制");
        Set<String> expected = central(zip, declaredCount), names = new HashSet<>(); long total = 0;
        if (!stage.mkdir()) throw new IOException("无法创建资源暂存目录");
        try (ZipInputStream input = new ZipInputStream(new FileInputStream(zip), StandardCharsets.UTF_8)) {
            ZipEntry entry;
            while ((entry = input.getNextEntry()) != null) {
                String name = assetPath(entry.getName()); if (!expected.contains(name) || !names.add(name) || names.size() > declaredCount || entry.isDirectory()) throw new IOException("ZIP 文件路径不一致");
                File outputFile = new File(stage, name); File parent = outputFile.getParentFile(); if (!parent.isDirectory() && !parent.mkdirs()) throw new IOException("无法创建资源目录");
                try (FileOutputStream output = new FileOutputStream(outputFile)) { total += copyLimited(input, output, declaredSize - total); }
            }
        }
        if (!names.equals(expected) || total != declaredSize) throw new IOException("ZIP 大小或文件数量不一致");
        byte[] manifestBytes = read(new FileInputStream(new File(stage, "asset-manifest.json")), META_LIMIT);
        if (!id.equals(hash(manifestBytes))) throw new IOException("资源清单标识错误"); JSONObject manifest = json(manifestBytes);
        integer(manifest, "protocolVersion", 1, 1);
        JSONObject files = manifest.getJSONObject("files"), sizes = manifest.getJSONObject("sizes"); Set<String> listed = new HashSet<>(); listed.add("asset-manifest.json");
        if (files.has("asset-manifest.json") || sizes.length() != files.length()) throw new IOException("资源清单文件无效");
        for (Iterator<String> it = files.keys(); it.hasNext();) {
            String name = assetPath(it.next()); listed.add(name); File file = new File(stage, name);
            if (!file.isFile() || file.length() != integer(sizes, name, 0, UNPACKED_LIMIT) || !hashFile(file).equals(digest(files, name))) throw new IOException("资源校验失败：" + name);
        }
        if (!listed.equals(names) || !listed.containsAll(REQUIRED)) throw new IOException("资源清单存在遗漏或额外文件");
    }
    static String imageMime(byte[] bytes) throws IOException {
        if (bytes.length == 0 || bytes.length > 10 * 1024 * 1024) throw new IOException("图片须小于 10 MiB");
        if (bytes.length >= 3 && (bytes[0]&255) == 255 && (bytes[1]&255) == 216 && (bytes[2]&255) == 255) return "image/jpeg";
        if (bytes.length >= 8 && Arrays.equals(Arrays.copyOf(bytes, 8), new byte[]{(byte)137,80,78,71,13,10,26,10})) return "image/png";
        if (bytes.length >= 12 && "RIFF".equals(new String(bytes,0,4,StandardCharsets.US_ASCII)) && "WEBP".equals(new String(bytes,8,4,StandardCharsets.US_ASCII))) return "image/webp";
        throw new IOException("请选择 JPEG、PNG 或 WebP 图片");
    }
}
