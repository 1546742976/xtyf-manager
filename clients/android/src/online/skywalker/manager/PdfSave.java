package online.skywalker.manager;

import java.io.IOException;
import java.util.Base64;

/** Save only bounded PDF bytes already read through the authenticated resource channel. */
final class PdfSave {
    static byte[] decode(String value) throws IOException {
        if (value.length() > 28 * 1024 * 1024) throw new IOException("PDF 超过保存限制");
        byte[] bytes;
        try { bytes = Base64.getDecoder().decode(value); }
        catch (IllegalArgumentException error) { throw new IOException("PDF 内容无效", error); }
        if (bytes.length < 5 || bytes.length > 20 * 1024 * 1024 || bytes[0] != '%' || bytes[1] != 'P' || bytes[2] != 'D' || bytes[3] != 'F' || bytes[4] != '-') throw new IOException("PDF 内容无效");
        return bytes;
    }
}
