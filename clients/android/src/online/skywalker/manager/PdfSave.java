package online.skywalker.manager;

import java.io.IOException;
import java.util.Base64;

/** Save only bounded PDF bytes already read through the authenticated resource channel. */
final class PdfSave {
    /** A single save's owned bytes. Cancellation also discards a late decode. */
    static final class Prepared {
        private byte[] bytes;
        private boolean cancelled;
        void prepare(String encoded) throws IOException {
            synchronized (this) { if (cancelled) throw new IOException("保存已取消"); }
            byte[] decoded = decode(encoded);
            synchronized (this) {
                if (cancelled) throw new IOException("保存已取消");
                bytes = decoded;
            }
        }
        synchronized byte[] take(boolean current) throws IOException {
            if (!current) cancel();
            if (cancelled || bytes == null) throw new IOException("页面或服务器已切换，或保存内容已失效");
            byte[] result = bytes; bytes = null; return result;
        }
        synchronized void cancel() { cancelled = true; bytes = null; }
    }

    static byte[] decode(String value) throws IOException {
        if (value.length() > 28 * 1024 * 1024) throw new IOException("PDF 超过保存限制");
        byte[] bytes;
        try { bytes = Base64.getDecoder().decode(value); }
        catch (IllegalArgumentException error) { throw new IOException("PDF 内容无效", error); }
        if (bytes.length < 5 || bytes.length > 20 * 1024 * 1024 || bytes[0] != '%' || bytes[1] != 'P' || bytes[2] != 'D' || bytes[3] != 'F' || bytes[4] != '-') throw new IOException("PDF 内容无效");
        return bytes;
    }
}
