use super::*;
use crate::updates::{Bundle, Store};
use std::borrow::Cow;

pub struct Runtime {
    pub store: Option<Store>, pub active: Option<Bundle>, pub server: String,
    pub generation: u64, pub view: String, pub ready: bool, pub fallback_count: u8,
    pub cancellation: CancellationToken, pub last_status: Value,
}
impl Runtime {
    pub fn new(directory: &std::path::Path, server: &str, generation: u64) -> Self {
        let mut runtime = Self { store: None, active: None, server: server.into(), generation,
            view: uuid::Uuid::new_v4().to_string(), ready: false, fallback_count: 0,
            cancellation: CancellationToken::new(), last_status: json!({"status":"current","message":"使用安装包内置页面","currentVersion":env!("CARGO_PKG_VERSION"),"pendingVersion":null,"installer":null}) };
        match Store::open(directory, server).and_then(|mut store| { let active = store.launch(false)?; Ok((store, active)) }) {
            Ok((store, active)) => { runtime.last_status = store.status("current", "页面已就绪", active.as_ref()); runtime.store = Some(store); runtime.active = active; }
            Err(error) => { runtime.last_status["status"] = json!("failed"); runtime.last_status["message"] = json!(error); }
        }
        runtime
    }
    pub fn status(&mut self, status: &str, message: &str) -> Value {
        self.last_status = if let Some(store) = &self.store { store.status(status, message, self.active.as_ref()) }
            else { json!({"status":status,"message":message,"currentVersion":env!("CARGO_PKG_VERSION"),"pendingVersion":null,"installer":null}) };
        self.last_status.clone()
    }
    fn root(&self) -> Option<PathBuf> { self.active.as_ref().and_then(|bundle| self.store.as_ref().map(|s| s.root(bundle))) }
}

pub async fn check(state: &AppState, retry: bool) -> Result<Value, String> {
    check_verified(state,retry,updates::parse_release).await
}
pub(crate) async fn check_verified(state: &AppState, retry: bool, verify: impl Fn(&[u8]) -> Result<Option<(updates::Release,String)>,String> + Send + Sync) -> Result<Value, String> {
    // A second caller receives the completed result of the in-flight check.
    let _lock = match state.update_gate.try_lock() { Ok(lock) => lock, Err(_) => {
        let _lock = state.update_gate.lock().await; return Ok(state.updates.lock().unwrap().last_status.clone());
    }};
    let (server, generation, cancellation) = {
        let runtime = state.updates.lock().unwrap();
        if runtime.store.is_none() { return Ok(runtime.last_status.clone()); }
        (runtime.server.clone(), runtime.generation, runtime.cancellation.clone())
    };
    let operation = async {
        if *state.server.lock().unwrap() != server { return Err("服务器已切换，更新已取消".into()); }
        let effective = state.effective_server();
        let session = state.session(&effective)?;
        let response = session.client.get(server_path(&effective, "/api/client/updates")?).send().await.map_err(|e|e.to_string())?;
        let metadata = updates::bounded_response(response, updates::METADATA_LIMIT).await?;
        let release = verify(&metadata)?;
        let bundle = {
            let mut runtime = state.updates.lock().unwrap();
            if runtime.generation != generation { return Err("服务器已切换，更新已取消".into()); }
            let Some((release, payload_hash)) = release else { return Ok(runtime.status("unavailable", "服务器尚未发布页面更新")); };
            let store = runtime.store.as_mut().unwrap(); store.accept(&release, &payload_hash)?;
            if let Err(reason) = updates::compatibility(&release.bundle) { return Ok(runtime.status("incompatible", &reason)); }
            if retry { store.retry(&release.bundle.id)?; }
            if store.state.quarantined.contains(&release.bundle.id) { return Ok(runtime.status("failed", "此版本上次启动失败，已回退；可手动重试")); }
            if store.state.pending.as_ref().is_some_and(|b| b.id == release.bundle.id) { return Ok(runtime.status("pending", "新版页面已下载，下次启动生效")); }
            if runtime.active.as_ref().is_some_and(|b| b.id == release.bundle.id) { return Ok(runtime.status("current", "当前页面已是最新版本")); }
            release.bundle
        };
        let response = session.client.get(server_path(&effective, &bundle.url)?).send().await.map_err(|e|e.to_string())?;
        let bytes = updates::bounded_response(response, bundle.size).await?;
        let mut runtime = state.updates.lock().unwrap();
        if runtime.generation != generation || cancellation.is_cancelled() { return Err("服务器已切换，更新已取消".into()); }
        runtime.store.as_mut().unwrap().stage(&bundle, &bytes)?;
        Ok(runtime.status("pending", "新版页面已下载，下次启动生效"))
    };
    let result = match futures_util::future::select(operation.boxed(), cancellation.cancelled().boxed()).await {
        futures_util::future::Either::Left((result,_)) => result,
        futures_util::future::Either::Right(_) => Err("服务器已切换，更新已取消".into()),
    };
    match result {
        Ok(value) => Ok(value), Err(error) => {
            let mut runtime = state.updates.lock().unwrap();
            if runtime.generation != generation { return Err(error); }
            Ok(runtime.status("failed", &error))
        }
    }
}

fn mime(path: &str) -> &'static str {
    match path.rsplit('.').next().unwrap_or("") {
        "html" => "text/html; charset=utf-8", "js"|"cjs"|"mjs" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8", "json"|"map" => "application/json", "svg" => "image/svg+xml",
        "png" => "image/png", "jpg"|"jpeg" => "image/jpeg", "webp" => "image/webp", "ico" => "image/x-icon",
        "woff" => "font/woff", "woff2" => "font/woff2", "ttf" => "font/ttf", "wasm" => "application/wasm", _ => "application/octet-stream",
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn signed_pdf_modules_keep_javascript_mime_with_nosniff() {
        for path in ["vendor/pdfjs/pdf.mjs", "vendor/pdfjs/pdf.worker.mjs", "pdf-viewer.js"] {
            assert_eq!(super::mime(path), "text/javascript; charset=utf-8");
        }
        assert_eq!(super::mime("vendor/pdfjs/wasm/openjpeg.wasm"), "application/wasm");
    }
}

/// Captured root never changes; close the old view before creating its successor.
pub fn create_view(app: &tauri::AppHandle) -> Result<(), String> {
    let state = app.state::<AppState>();
    let (root, view_id) = { let runtime = state.updates.lock().unwrap(); (runtime.root(), runtime.view.clone()) };
    let window = app.get_window("workspace").ok_or("主窗口不存在")?;
    let size = window.inner_size().map_err(|e|e.to_string())?;
    let csp: Value = serde_json::from_str(include_str!("../tauri.conf.json")).map_err(|e|e.to_string())?;
    let csp = csp["app"]["security"]["csp"].as_str().unwrap_or("").to_string();
    let mut builder = tauri::webview::WebviewBuilder::new("main", WebviewUrl::App("index.html".into()))
        .initialization_script(format!("Object.defineProperty(window,'__XTYF_VIEW_TOKEN__',{{value:{},writable:false,configurable:false}});",json!(view_id)))
        .on_permission_request(|view, kind| crate::camera::request(view, kind, None))
        .on_navigation(local_url).on_new_window(|_,_|tauri::webview::NewWindowResponse::Deny)
        .on_web_resource_request(move |request, response| {
            if !Url::parse(&request.uri().to_string()).is_ok_and(|u| local_url(&u)) { return; }
            response.headers_mut().insert("Cache-Control", "no-store".parse().unwrap());
            let Some(root) = &root else { return; };
            let path = request.uri().path().trim_start_matches('/');
            let path = if path.is_empty() { "index.html" } else { path };
            let bytes = if updates::safe_path(path) { fs::read(root.join(path)).ok() } else { None };
            // No packaged fallback: mixing a new page with old scripts is unsafe.
            *response.status_mut() = if bytes.is_some() { tauri::http::StatusCode::OK } else { tauri::http::StatusCode::NOT_FOUND };
            *response.body_mut() = Cow::Owned(bytes.unwrap_or_default());
            response.headers_mut().remove("Content-Length");
            response.headers_mut().insert("Content-Type", mime(path).parse().unwrap());
            response.headers_mut().insert("Content-Security-Policy", csp.parse().unwrap());
            response.headers_mut().insert("X-Content-Type-Options", "nosniff".parse().unwrap());
        });
    if smoke_directory().is_some() { builder = builder.data_directory(state.directory.join("webview")); }
    let view = window.add_child(builder, tauri::PhysicalPosition::new(0,0), size).map_err(|e|e.to_string())?;
    crate::camera::configure(&view)?;
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_secs(15)).await;
        let state = handle.state::<AppState>();
        let reload = {
            let mut runtime = state.updates.lock().unwrap();
            if runtime.view != view_id || runtime.ready || runtime.active.is_none() { false } else {
                let fallback = runtime.store.as_mut().and_then(|store| store.failed().ok()).flatten();
                runtime.active = if runtime.fallback_count == 0 { fallback } else { None };
                runtime.fallback_count += 1;
                let next = runtime.active.clone();
                if let Some(store) = &mut runtime.store { if store.begin_trial(next).is_err() { runtime.active = None; } }
                runtime.view = uuid::Uuid::new_v4().to_string();
                runtime.status("failed", "新版页面启动失败，已自动回退"); true
            }
        };
        if reload { schedule_reload(&handle); }
    });
    Ok(())
}
pub fn schedule_reload(app: &tauri::AppHandle) {
    let handle = app.clone();
    // Return the IPC response before disposing of its originating webview.
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_millis(100)).await;
        let next = handle.clone();
        let _ = handle.run_on_main_thread(move || {
            if let Some(view) = next.get_webview("main") { let _ = view.close(); }
            if let Err(error) = create_view(&next) { eprintln!("页面加载失败：{error}"); }
        });
    });
}
pub fn ready(state: &AppState, view_token: &str) -> Result<Value, String> {
    let mut runtime = state.updates.lock().unwrap();
    if runtime.view != view_token { return Err("旧页面的初始化确认已忽略".into()); }
    let id = runtime.active.as_ref().map(|b|b.id.clone());
    if let Some(store) = &mut runtime.store { store.ready(id.as_deref())?; }
    runtime.ready = true; Ok(json!({"ok":true}))
}
pub fn apply(app: &tauri::AppHandle, state: &AppState) -> Result<Value, String> {
    let mut runtime = state.updates.lock().unwrap();
    if !runtime.ready { return Err("页面尚未完成初始化，请稍后重试".into()); }
    let store = runtime.store.as_mut().ok_or("本地更新状态不可用")?;
    if store.state.pending.is_none() { return Err("没有已下载的待更新页面".into()); }
    runtime.active = store.launch(false)?; runtime.ready = false; runtime.fallback_count = 0;
    runtime.view = uuid::Uuid::new_v4().to_string();
    let value = runtime.status("current", "正在重载页面"); drop(runtime); schedule_reload(app); Ok(value)
}
