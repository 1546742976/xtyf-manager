package online.skywalker.manager;

import java.io.IOException;
import java.net.URI;

/** Shared origin checks for the unprivileged website view and its download channel. */
final class WebsitePolicy {
    static boolean trusted(String origin, String value) {
        try {
            URI target = URI.create(value); String withoutFragment = target.toString().split("#", 2)[0];
            return value.indexOf('\\') < 0 && NativeHttpClient.sameOrigin(URI.create(origin), URI.create(withoutFragment));
        } catch (IllegalArgumentException | NullPointerException error) { return false; }
    }
    static boolean blob(String origin, String value) {
        return value != null && value.startsWith("blob:") && trusted(origin, value.substring(5));
    }
    static String filename(String value) {
        String result = value == null ? "download" : value.replaceAll("[\\\\/\\x00-\\x1f]", "_");
        return result.isEmpty() || result.equals(".") || result.equals("..") ? "download" : result.substring(0, Math.min(180, result.length()));
    }
    static void size(long size) throws IOException {
        if (size < 0 || size > 256L * 1024 * 1024) throw new IOException("下载文件不能超过 256 MiB");
    }
}
