use super::*;
use tauri::webview::{DownloadEvent, NewWindowResponse, WebviewBuilder};

/// Discovery is anonymous. A server may upgrade its own HTTP origin, but never
/// redirect credentials to another host/port or downgrade an HTTPS connection.
pub(crate) fn discovery_redirect(from: &Url, to: &Url) -> bool {
    if !to.username().is_empty() || to.password().is_some() || to.host_str() != from.host_str() {
        return false;
    }
    if from.origin() == to.origin() { return matches!(to.scheme(), "http" | "https"); }
    from.scheme() == "http" && to.scheme() == "https"
        && ((from.port_or_known_default() == Some(80) && to.port_or_known_default() == Some(443))
            || (from.port().is_some() && from.port() == to.port()))
}

async fn discover(configured: &str, path: &str) -> Result<String, String> {
    let client = Client::builder().redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(20)).timeout(Duration::from_secs(20))
        .user_agent(format!("XTYFManager/{}", env!("CARGO_PKG_VERSION")))
        .build().map_err(|e| e.to_string())?;
    let mut url = server_path(configured, path)?;
    for redirects in 0..=5 {
        let response = client.get(url.clone()).header("X-XTYF-Client-Protocol", "1")
            .send().await.map_err(|e| e.to_string())?;
        if !response.status().is_redirection() { return Ok(url.origin().ascii_serialization()); }
        if redirects == 5 { return Err("服务器重定向次数过多".into()); }
        let location = response.headers().get(header::LOCATION).and_then(|h| h.to_str().ok()).ok_or("重定向缺少地址")?;
        let next = url.join(location).map_err(|e| e.to_string())?;
        if !discovery_redirect(&url, &next) { return Err("服务器跳转到其他主机、端口或不安全地址，请在设置中确认服务器地址".into()); }
        url = next;
    }
    Err("服务器重定向次数过多".into())
}

pub struct Prepared {
    configured: String,
    effective: String,
    generation: u64,
}

pub async fn prepare(state: &AppState) -> Result<Prepared, String> {
    let configured = state.server.lock().unwrap().clone();
    let (generation, cancellation) = {
        let runtime = state.updates.lock().unwrap(); (runtime.generation, runtime.cancellation.clone())
    };
    // A missing/timing-out bootstrap may coexist with a usable website. Discover
    // its root separately, without introducing credentials or changing settings.
    let operation = async {
        let _gate = state.connection_gate.lock().await;
        if state.updates.lock().unwrap().generation != generation || *state.server.lock().unwrap() != configured { return Err("服务器已切换，网站连接已取消".into()); }
        let cached = state.effective_server.lock().unwrap().as_ref().filter(|(server, _)| server == &configured).map(|(_, effective)| effective.clone());
        let effective = match cached { Some(effective) => effective, None => discover(&configured, "/").await? };
        let runtime = state.updates.lock().unwrap();
        if runtime.generation != generation || cancellation.is_cancelled() || *state.server.lock().unwrap() != configured { return Err("服务器已切换，网站连接已取消".into()); }
        *state.effective_server.lock().unwrap() = Some((configured.clone(), effective.clone()));
        Ok(Prepared { configured: configured.clone(), effective, generation })
    };
    let result = match futures_util::future::select(operation.boxed(), cancellation.cancelled().boxed()).await {
        futures_util::future::Either::Left((result, _)) => result,
        futures_util::future::Either::Right(_) => Err("服务器已切换，网站连接已取消".into()),
    };
    result
}

pub async fn probe(state: &AppState) -> Result<Value, String> {
    let configured = state.server.lock().unwrap().clone();
    let (generation, cancellation) = {
        let runtime = state.updates.lock().unwrap(); (runtime.generation, runtime.cancellation.clone())
    };
    let operation = async {
        let _gate = state.connection_gate.lock().await;
        if state.updates.lock().unwrap().generation != generation || *state.server.lock().unwrap() != configured { return Err("服务器已切换，连接检查已取消".into()); }
        let effective = discover(&configured, "/api/client/bootstrap").await?;
        {
            let runtime = state.updates.lock().unwrap();
            if runtime.generation != generation || cancellation.is_cancelled() || *state.server.lock().unwrap() != configured { return Err("服务器已切换，连接检查已取消".into()); }
            *state.effective_server.lock().unwrap() = Some((configured.clone(), effective.clone()));
        }
        // Only after the origin is pinned may the existing native cookie jar be
        // used. Website cookies remain in the separate WebView profile.
        let response = send_request(state, &json!({"path":"/api/client/bootstrap"})).await?;
        let response = response_value(response, false).await?;
        if cancellation.is_cancelled() || state.updates.lock().unwrap().generation != generation {
            return Err("服务器已切换，连接检查已取消".into());
        }
        Ok(json!({"serverUrl":configured,"effectiveServerUrl":effective,"response":response}))
    };
    let result = match futures_util::future::select(operation.boxed(), cancellation.cancelled().boxed()).await {
        futures_util::future::Either::Left((result, _)) => result,
        futures_util::future::Either::Right(_) => Err("服务器已切换，连接检查已取消".into()),
    };
    result
}

pub(crate) fn website_resource(origin: &Url, url: &Url) -> bool {
    (matches!(url.scheme(), "http" | "https") || url.scheme() == "blob") && url.origin() == origin.origin()
}

fn browse_elsewhere(app: &tauri::AppHandle, url: &Url) {
    if !matches!(url.scheme(), "http" | "https") { return; }
    let handle = app.clone(); let address = url.to_string();
    let _ = app.run_on_main_thread(move || { let _ = external(&handle, &address); });
}

fn download_error(app: &tauri::AppHandle, generation: u64, epoch: u64, message: String) {
    let app = app.clone();
    // Never open a modal dialog from a WebView download callback. Ignore late
    // failures from a closed website or a server that is no longer selected.
    tauri::async_runtime::spawn(async move {
        let state = app.state::<AppState>();
        if state.updates.lock().unwrap().generation != generation
            || state.website_epoch.load(std::sync::atomic::Ordering::SeqCst) != epoch
            || app.get_webview("website-page").is_none() { return; }
        rfd::AsyncMessageDialog::new().set_title("网站文件下载失败")
            .set_description(message).set_level(rfd::MessageLevel::Error)
            .set_buttons(rfd::MessageButtons::Ok).show().await;
    });
}

fn save_download(source: &std::path::Path, target: &std::path::Path, current: impl Fn() -> bool) -> Result<(), String> {
    // Stage beside the selected target so publishing is atomic even when the
    // private download cache and user's destination live on different drives.
    let staged = target.with_extension(format!("{}.part", uuid::Uuid::new_v4()));
    let result = (|| {
        fs::copy(source, &staged).map_err(|e| e.to_string())?;
        fs::OpenOptions::new().write(true).open(&staged).and_then(|file| file.sync_all()).map_err(|e| e.to_string())?;
        if !current() { return Err("网站已关闭或服务器已切换，保存已取消".into()); }
        fs::rename(&staged, target).map_err(|e| e.to_string())
    })();
    if result.is_err() { let _ = fs::remove_file(&staged); }
    let _ = fs::remove_file(source);
    result
}

pub fn resize(app: &tauri::AppHandle, size: tauri::PhysicalSize<u32>) {
    if let Some(view) = app.get_webview("website-page") {
        let _ = view.set_position(tauri::PhysicalPosition::new(0, 0));
        let _ = view.set_size(size);
    }
}

pub fn close(app: &tauri::AppHandle) {
    let state = app.state::<AppState>();
    state.website_epoch.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    let downloads:Vec<_> = state.website_files.lock().unwrap().drain().collect();
    if let Some(view) = app.get_webview("website-page") { let _ = view.close(); }
    // Tauri suppresses DownloadFinished after a view has been removed. Retain
    // its private paths here and clean them after the engine releases its files.
    if !downloads.is_empty() {
        tauri::async_runtime::spawn(async move {
            for attempt in 0..20 {
                let mut busy = false;
                for path in &downloads {
                    for candidate in [path.clone(), path.with_extension("part.crdownload")] {
                        if let Err(error) = fs::remove_file(candidate) { if error.kind() != std::io::ErrorKind::NotFound { busy = true; } }
                    }
                }
                if !busy && attempt > 0 { break; }
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
        });
    }
    if let Some(view) = app.get_webview("main") { let _ = view.show(); }
}

pub fn return_local(app: &tauri::AppHandle, intent: &str) -> Result<Value, String> {
    if !matches!(intent, "settings" | "retry" | "return") { return Err("返回操作无效".into()); }
    close(app);
    let view = app.get_webview("main").ok_or("本地页面不可用")?;
    view.eval(&format!("window.dispatchEvent(new CustomEvent('xtyf-website-return',{{detail:{{intent:{}}}}}));", json!(intent))).map_err(|e| e.to_string())?;
    view.set_focus().map_err(|e| e.to_string())?;
    Ok(json!({"returned":true}))
}

pub fn open(app: &tauri::AppHandle, state: &AppState, prepared: Prepared) -> Result<Value, String> {
    let runtime = state.updates.lock().unwrap();
    let configured = state.server.lock().unwrap();
    if runtime.generation != prepared.generation || *configured != prepared.configured { return Err("服务器已切换，网站连接已取消".into()); }
    let generation = runtime.generation;
    drop(runtime);
    // Keep the configured address fixed until the view exists. A subsequent
    // server switch closes this view after replacing its connection generation.
    if app.get_webview("website-page").is_some() { return Ok(json!({"opened":true})); }
    let origin = server_path(&prepared.effective, "/")?;
    let epoch = state.website_epoch.load(std::sync::atomic::Ordering::SeqCst);
    let window = app.get_window("workspace").ok_or("主窗口不存在")?;
    let size = window.inner_size().map_err(|e| e.to_string())?;
    let navigate_app = app.clone(); let navigate_origin = origin.clone();
    let popup_app = app.clone(); let popup_origin = origin.clone();
    let download_origin = origin.clone(); let download_app = app.clone();
    let download_directory = state.directory.join("website-downloads");
    fs::create_dir_all(&download_directory).map_err(|e| e.to_string())?;
    let pending = Arc::new(Mutex::new(HashMap::<PathBuf, (String, String)>::new()));
    let camera_origin = origin.clone();
    let mut builder = WebviewBuilder::new("website-page", WebviewUrl::External(origin.clone()))
        .on_navigation(move |url| {
            if website_resource(&navigate_origin, url) { true }
            else { browse_elsewhere(&navigate_app, url); false }
        })
        .on_permission_request(move |view, kind| crate::camera::request(view, kind, Some(&camera_origin)))
        .on_new_window(move |url, _| {
            if website_resource(&popup_origin, &url) {
                let handle = popup_app.clone();
                let _ = popup_app.run_on_main_thread(move || { if let Some(view) = handle.get_webview("website-page") { let _ = view.navigate(url); } });
            } else { browse_elsewhere(&popup_app, &url); }
            NewWindowResponse::Deny
        })
        .on_download(move |_, event| {
            let current = || download_app.state::<AppState>().updates.lock().unwrap().generation == generation
                && download_app.state::<AppState>().website_epoch.load(std::sync::atomic::Ordering::SeqCst) == epoch
                && download_app.get_webview("website-page").is_some();
            match event {
                DownloadEvent::Requested { url, destination } => {
                    if !current() || !website_resource(&download_origin, &url) { return false; }
                    let name = destination.file_name().and_then(|n| n.to_str()).unwrap_or("download").to_string();
                    // WebView2 cannot display a modal dialog inside DownloadStarting.
                    // Download privately with its cookies, then ask asynchronously.
                    let temporary = download_directory.join(format!("{}.part", uuid::Uuid::new_v4()));
                    download_app.state::<AppState>().website_files.lock().unwrap().insert(temporary.clone());
                    pending.lock().unwrap().insert(temporary.clone(), (name, url.to_string()));
                    *destination = temporary;
                    true
                },
                DownloadEvent::Finished { path, success, url } => {
                    if let Some(path) = path {
                        if let Some((name, _)) = pending.lock().unwrap().remove(&path) {
                            if !success || !current() {
                                let _ = fs::remove_file(&path);
                                download_app.state::<AppState>().website_files.lock().unwrap().remove(&path);
                                if !success && current() {
                                    download_error(&download_app, generation, epoch, "文件下载失败，请重试；原文件未更改".into());
                                }
                            } else {
                                let app = download_app.clone();
                                tauri::async_runtime::spawn(async move {
                                    if app.state::<AppState>().website_epoch.load(std::sync::atomic::Ordering::SeqCst) != epoch { let _ = fs::remove_file(&path); return; }
                                    let selected = rfd::AsyncFileDialog::new().set_title("保存网站文件").set_file_name(&name).save_file().await;
                                    let state = app.state::<AppState>();
                                    if state.updates.lock().unwrap().generation != generation || state.website_epoch.load(std::sync::atomic::Ordering::SeqCst) != epoch {
                                        let _ = fs::remove_file(&path); state.website_files.lock().unwrap().remove(&path); return;
                                    }
                                    if let Some(target) = selected {
                                        if let Err(error) = save_download(&path, target.path(), || state.updates.lock().unwrap().generation == generation && state.website_epoch.load(std::sync::atomic::Ordering::SeqCst) == epoch) {
                                            download_error(&app, generation, epoch, format!("文件保存失败，原文件未更改：{error}"));
                                        }
                                    } else { let _ = fs::remove_file(&path); }
                                    state.website_files.lock().unwrap().remove(&path);
                                });
                            }
                        }
                    } else if !success {
                        let mut files = pending.lock().unwrap();
                        let paths:Vec<_> = files.iter().filter(|(_, (_, source))| source == url.as_str()).map(|(path, _)| path.clone()).collect();
                        for path in paths { files.remove(&path); let _ = fs::remove_file(&path); download_app.state::<AppState>().website_files.lock().unwrap().remove(&path); }
                        download_error(&download_app, generation, epoch, "文件下载失败，请重试".into());
                    }
                    true
                },
                _ => false,
            }
        });
    // Website cookies persist in the WebView profile, independently of native
    // reqwest sessions. Sharing the local shell's WebView environment also
    // preserves normal cookie behavior for same-origin windows on Windows.
    if smoke_directory().is_some() { builder = builder.data_directory(state.directory.join("webview")); }
    match window.add_child(builder, tauri::PhysicalPosition::new(0, 0), size) {
        Ok(view) => {
            if let Err(error) = crate::camera::configure(&view) { close(app); return Err(error); }
            if let Some(local) = app.get_webview("main") { let _ = local.hide(); }
            let _ = view.set_focus();
            Ok(json!({"opened":true,"effectiveServerUrl":origin.origin().ascii_serialization()}))
        },
        Err(error) => { close(app); Err(error.to_string()) },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn safe_discovery_redirects() {
        for (from, to) in [("http://example.com/a", "https://example.com/b"), ("http://example.com:8443/a", "https://example.com:8443/b"), ("https://example.com/a", "https://example.com/login")] {
            assert!(discovery_redirect(&Url::parse(from).unwrap(), &Url::parse(to).unwrap()), "{from} -> {to}");
        }
        for (from, to) in [("https://example.com", "http://example.com"), ("http://example.com", "https://other.example"), ("http://example.com:8520", "https://example.com"), ("http://example.com", "https://example.com:8443"), ("http://example.com", "https://user@example.com")] {
            assert!(!discovery_redirect(&Url::parse(from).unwrap(), &Url::parse(to).unwrap()), "{from} -> {to}");
        }
    }
    #[test]
    fn website_download_origin_includes_only_own_blob() {
        let origin = Url::parse("https://example.com/").unwrap();
        for url in ["https://example.com/attachment", "blob:https://example.com/synthetic"] { assert!(website_resource(&origin, &Url::parse(url).unwrap())); }
        for url in ["https://other.example/a", "blob:https://other.example/a", "blob:null/abc", "file:///secret", "data:text/plain,test"] { assert!(!website_resource(&origin, &Url::parse(url).unwrap())); }
    }
    #[test]
    fn download_publish_replaces_atomically_and_cancellation_preserves_existing_file() {
        let directory = tempfile::tempdir().unwrap();
        let source = directory.path().join("incoming.part"); let target = directory.path().join("saved.txt");
        fs::write(&source, "new").unwrap(); fs::write(&target, "old").unwrap();
        save_download(&source, &target, || true).unwrap();
        assert_eq!(fs::read_to_string(&target).unwrap(), "new"); assert!(!source.exists());
        fs::write(&source, "must not replace").unwrap();
        assert!(save_download(&source, &target, || false).is_err());
        assert_eq!(fs::read_to_string(&target).unwrap(), "new"); assert!(!source.exists());
        assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
        fs::write(&source, "failed path").unwrap();
        assert!(save_download(&source, &directory.path().join("missing/saved.txt"), || true).is_err());
        assert_eq!(fs::read_to_string(&target).unwrap(), "new"); assert!(!source.exists());
    }
}
