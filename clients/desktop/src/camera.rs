use tauri::webview::{PermissionKind, PermissionResponse};
use url::Url;

pub fn configure(view: &tauri::Webview) -> Result<(), String> {
    #[cfg(target_os = "linux")]
    view.with_webview(|platform| {
        use webkit2gtk::{SettingsExt, WebViewExt};
        if let Some(settings) = platform.inner().settings() {
            settings.set_enable_media_stream(true);
        }
    }).map_err(|error| error.to_string())?;
    #[cfg(not(target_os = "linux"))]
    let _ = view;
    Ok(())
}

fn allowed(document: &Url, server: Option<&Url>, kind: PermissionKind) -> bool {
    if kind != PermissionKind::Camera { return false; }
    match server {
        None => super::local_url(document),
        Some(origin) => document.scheme() == "https" && document.origin() == origin.origin()
            && document.username().is_empty() && document.password().is_none(),
    }
}

pub fn request(view: tauri::Webview, kind: PermissionKind, server: Option<&Url>) -> PermissionResponse {
    let Ok(document) = view.url() else { return PermissionResponse::Deny; };
    if !allowed(&document, server, kind) { return PermissionResponse::Deny; }
    // WebView2 supplies its native consent prompt. WebKitGTK defaults to deny,
    // so Linux needs an explicit consent dialog before granting video capture.
    #[cfg(not(target_os = "linux"))]
    { PermissionResponse::Default }
    #[cfg(target_os = "linux")]
    {
        let granted = rfd::MessageDialog::new().set_title("摄像头权限")
            .set_description("当前巡天御风页面请求使用摄像头进行扫码。是否允许？")
            .set_buttons(rfd::MessageButtons::YesNo).show() == rfd::MessageDialogResult::Yes;
        if granted && view.url().is_ok_and(|current| current == document) {
            PermissionResponse::Allow
        } else { PermissionResponse::Deny }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_camera_on_local_or_selected_https_site() {
        let local = Url::parse("http://tauri.localhost/index.html").unwrap();
        let server = Url::parse("https://example.test").unwrap();
        assert!(allowed(&local, None, PermissionKind::Camera));
        assert!(allowed(&server.join("/assets").unwrap(), Some(&server), PermissionKind::Camera));
        assert!(!allowed(&local, None, PermissionKind::Microphone));
        for document in ["https://evil.test/assets", "http://example.test/assets", "https://example.test:8443/assets", "https://user@example.test/assets"] {
            assert!(!allowed(&Url::parse(document).unwrap(), Some(&server), PermissionKind::Camera));
        }
        assert!(!allowed(&server, None, PermissionKind::Camera));
    }
}
