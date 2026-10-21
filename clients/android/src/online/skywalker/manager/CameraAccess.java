package online.skywalker.manager;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.webkit.PermissionRequest;
import android.webkit.WebView;
import java.util.function.BooleanSupplier;

/** Only video, only the configured HTTPS document, rechecked after the OS prompt. */
final class CameraAccess {
    static final int REQUEST = 41;
    private final Activity activity;
    private PermissionRequest pending;
    private WebView owner;
    private BooleanSupplier valid;
    CameraAccess(Activity activity) { this.activity = activity; }
    void request(WebView view, PermissionRequest request, String origin) {
        cancel(null);
        String document = view.getUrl();
        BooleanSupplier check = () -> document != null && document.equals(view.getUrl())
            && origin.startsWith("https://") && WebsitePolicy.trusted(origin, document)
            && WebsitePolicy.trusted(origin, request.getOrigin().toString());
        String[] resources = request.getResources();
        if (resources.length != 1 || !PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resources[0]) || !check.getAsBoolean()) {
            request.deny(); return;
        }
        pending = request; owner = view; valid = check;
        if (activity.checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) finish(true);
        else activity.requestPermissions(new String[]{Manifest.permission.CAMERA}, REQUEST);
    }
    void finish(boolean granted) {
        if (pending == null) return;
        PermissionRequest request = pending; BooleanSupplier check = valid;
        pending = null; owner = null; valid = null;
        if (granted && check.getAsBoolean()) request.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
        else request.deny();
    }
    void cancel(WebView view) { if (pending != null && (view == null || owner == view)) finish(false); }
    void cancelled(PermissionRequest request) {
        if (pending == request) { pending = null; owner = null; valid = null; }
    }
}
