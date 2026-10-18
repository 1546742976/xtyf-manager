#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use base64::{engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD}, Engine};
use futures_util::{StreamExt, FutureExt};
use reqwest::{header, multipart, Client, Method};
use reqwest_cookie_store::{CookieStore, CookieStoreMutex};
use serde_json::{json, Value};
use std::{collections::HashMap, fs, io::{BufReader, BufWriter, Write}, path::PathBuf, sync::{Arc, Mutex}, time::Duration};
use tauri::{Manager, State, WebviewUrl};
use tokio::io::AsyncWriteExt;
use tokio_util::sync::CancellationToken;
use url::Url;
use sha2::{Digest, Sha256};
mod updates;
mod native_updates;
mod website;

struct Session {
    client: Client,
    cookies: Arc<CookieStoreMutex>,
    csrf: Mutex<String>,
    path: PathBuf,
}
struct AppState {
    server: Mutex<String>,
    effective_server: Mutex<Option<(String, String)>>,
    website_epoch: std::sync::atomic::AtomicU64,
    website_menu_open: std::sync::atomic::AtomicBool,
    connection_gate: tokio::sync::Mutex<()>,
    website_files: Mutex<std::collections::HashSet<PathBuf>>,
    directory: PathBuf,
    sessions: Mutex<HashMap<String, Arc<Session>>>,
    files: Mutex<HashMap<String, PathBuf>>,
    requests: Mutex<HashMap<String, CancellationToken>>,
    updates: Mutex<native_updates::Runtime>,
    update_gate: tokio::sync::Mutex<()>,
}
fn text<'a>(value: &'a Value, key: &str) -> &'a str { value.get(key).and_then(Value::as_str).unwrap_or("") }
fn smoke_directory() -> Option<PathBuf> {
    #[cfg(debug_assertions)] { std::env::var_os("XTYF_SMOKE_DIR").map(PathBuf::from) }
    #[cfg(not(debug_assertions))] { None }
}
fn normalize_server(input: &str) -> Result<String, String> {
    let url = Url::parse(input.trim()).map_err(|_| "请输入完整的 HTTP/HTTPS 网站根地址")?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() || !url.username().is_empty()
        || url.password().is_some() || url.query().is_some() || url.fragment().is_some() || url.path() != "/" {
        return Err("服务器地址不能包含账号、密码、路径、查询参数或片段".into());
    }
    Ok(url.origin().ascii_serialization())
}
fn server_path(server: &str, path: &str) -> Result<Url, String> {
    if !path.starts_with('/') || path.starts_with("//") || path.contains('\\') {
        return Err("请求必须使用当前服务器内的相对路径".into());
    }
    let base = Url::parse(server).map_err(|e| e.to_string())?;
    let result = base.join(path).map_err(|e| e.to_string())?;
    if result.origin() != base.origin() { return Err("禁止向站外地址发送业务请求".into()); }
    Ok(result)
}
fn local_url(url: &Url) -> bool {
    url.port().is_none() && url.username().is_empty() && url.password().is_none()
        && ((url.scheme() == "tauri" && url.host_str() == Some("localhost"))
            || (matches!(url.scheme(), "http" | "https") && url.host_str() == Some("tauri.localhost")))
}
impl AppState {
    fn effective_server(&self) -> String {
        let server = self.server.lock().unwrap().clone();
        self.effective_server.lock().unwrap().as_ref()
            .filter(|(configured, _)| configured == &server)
            .map(|(_, effective)| effective.clone()).unwrap_or(server)
    }
    fn session(&self, server: &str) -> Result<Arc<Session>, String> {
        let mut sessions = self.sessions.lock().unwrap();
        if let Some(session) = sessions.get(server) { return Ok(session.clone()); }
        let path = self.directory.join(format!("session-{}.json", URL_SAFE_NO_PAD.encode(server)));
        let cookies:CookieStore = fs::File::open(&path).ok().and_then(|f| serde_json::from_reader(BufReader::new(f)).ok()).unwrap_or_default();
        let cookies = Arc::new(CookieStoreMutex::new(cookies));
        let client = Client::builder().cookie_provider(cookies.clone()).connect_timeout(Duration::from_secs(20))
            .timeout(Duration::from_secs(180)).user_agent(format!("XTYFManager/{}", env!("CARGO_PKG_VERSION")))
            .redirect(reqwest::redirect::Policy::none()).build().map_err(|e| e.to_string())?;
        let session = Arc::new(Session {client, cookies, csrf: Mutex::new(String::new()), path});
        sessions.insert(server.to_string(), session.clone());
        Ok(session)
    }
}
impl Session {
    fn update(&self, response: &reqwest::Response) -> Result<(), String> {
        if let Some(token) = response.headers().get("X-CSRF-Token").and_then(|h| h.to_str().ok()) {
            *self.csrf.lock().unwrap() = token.to_string();
        }
        let cookies = self.cookies.lock().unwrap();
        let file = fs::File::create(&self.path).map_err(|e| e.to_string())?;
        let mut writer = BufWriter::new(file);
        serde_json::to_writer(&mut writer, &*cookies).map_err(|e| e.to_string())?;
        writer.flush().map_err(|e| e.to_string())
    }
}
async fn send_request(state: &AppState, args: &Value) -> Result<reqwest::Response, String> {
    let server = state.effective_server();
    let session = state.session(&server)?;
    let url = server_path(&server, text(args, "path"))?;
    let method = Method::from_bytes(args.get("method").and_then(Value::as_str).unwrap_or("GET").as_bytes()).map_err(|e| e.to_string())?;
    if !matches!(method, Method::GET | Method::HEAD | Method::POST | Method::PUT | Method::PATCH | Method::DELETE | Method::OPTIONS) {
        return Err("不支持的请求方法".into());
    }
    let mut request = session.client.request(method.clone(), url).header("X-XTYF-Client-Protocol", "1");
    if let Some(headers) = args.get("headers").and_then(Value::as_object) {
        for (key, value) in headers {
            if ["content-type", "accept", "if-match", "range", "x-requested-with"].contains(&key.to_lowercase().as_str()) {
                if let Some(value) = value.as_str() { request = request.header(key, value); }
            }
        }
    }
    if !matches!(method, Method::GET | Method::HEAD | Method::OPTIONS) {
        if session.csrf.lock().unwrap().is_empty() {
            let response = session.client.get(format!("{server}/api/csrf")).header("X-XTYF-Client-Protocol","1").send().await.map_err(|e| e.to_string())?;
            session.update(&response)?;
            if !response.status().is_success() { return Err("无法建立服务器会话".into()); }
        }
        let token = session.csrf.lock().unwrap().clone();
        request = request.header("X-CSRF-Token", token);
    }
    if let Some(multipart) = args.get("multipart") {
        let mut form = multipart::Form::new();
        if let Some(fields) = multipart.get("fields").and_then(Value::as_array) {
            for field in fields { form = form.text(text(field, "name").to_string(), text(field, "value").to_string()); }
        }
        if let Some(files) = multipart.get("files").and_then(Value::as_array) {
            for file in files {
                let path = state.files.lock().unwrap().get(text(file, "handle")).cloned().ok_or("文件选择已失效，请重新选择")?;
                let data = tokio::fs::File::open(&path).await.map_err(|e| e.to_string())?;
                let length = data.metadata().await.map_err(|e| e.to_string())?.len();
                let body = reqwest::Body::wrap_stream(tokio_util::io::ReaderStream::new(data));
                let part = multipart::Part::stream_with_length(body, length).file_name(path.file_name().unwrap().to_string_lossy().into_owned())
                    .mime_str(if text(file, "mime").is_empty() {"application/octet-stream"} else {text(file, "mime")}).map_err(|e| e.to_string())?;
                form = form.part(text(file, "field").to_string(), part);
            }
        }
        request = request.multipart(form);
    } else if let Some(body) = args.get("body").and_then(Value::as_str) { request = request.body(body.to_string()); }
    let mut response = request.send().await.map_err(|e| e.to_string())?;
    for redirects in 0..=5 {
        session.update(&response)?;
        if !response.status().is_redirection() { break; }
        if redirects == 5 || !matches!(method,Method::GET|Method::HEAD) {return Err("服务器返回了不允许的重定向，写入结果请刷新核对".into());}
        let location=response.headers().get(header::LOCATION).and_then(|h|h.to_str().ok()).ok_or("重定向缺少地址")?;
        let next=response.url().join(location).map_err(|e|e.to_string())?;
        if next.origin()!=Url::parse(&server).unwrap().origin() {return Err("禁止向站外地址转发业务请求".into());}
        response=session.client.request(method.clone(),next).header("X-XTYF-Client-Protocol","1").send().await.map_err(|e|e.to_string())?;
    }
    Ok(response)
}
async fn response_value(response: reqwest::Response, binary: bool) -> Result<Value, String> {
    let status = response.status().as_u16();
    let headers: HashMap<String, String> = response.headers().iter().filter(|(k,_)| *k != header::SET_COOKIE)
        .filter_map(|(k,v)| v.to_str().ok().map(|v|(k.to_string(),v.to_string()))).collect();
    let bytes = response.bytes().await.map_err(|e| e.to_string())?;
    Ok(json!({"status":status,"headers":headers,"body":if binary {STANDARD.encode(&bytes)} else {String::from_utf8_lossy(&bytes).into_owned()},"encoding":if binary {"base64"} else {"text"}}))
}
async fn pick_files(state: &AppState, args: &Value) -> Result<Value, String> {
    let generation = state.updates.lock().unwrap().generation;
    let mut dialog = rfd::AsyncFileDialog::new().set_title("选择文件");
    let extensions:Vec<&str>=text(args,"accept").split(',').filter_map(|s|s.trim().strip_prefix('.')).filter(|s|!s.is_empty()).collect();
    if !extensions.is_empty(){dialog=dialog.add_filter("支持的文件",&extensions);}
    let selected = if args.get("multiple").and_then(Value::as_bool).unwrap_or(false) {dialog.pick_files().await.unwrap_or_default()}
        else {dialog.pick_file().await.into_iter().collect()};
    let runtime = state.updates.lock().unwrap();
    if runtime.generation != generation { return Err("服务器已切换，请重新选择文件".into()); }
    let mut files = state.files.lock().unwrap();
    let mut result = Vec::new();
    for selected in selected {
        let path = selected.path().to_path_buf();
        let handle = uuid::Uuid::new_v4().to_string();
        result.push(json!({"handle":handle,"name":selected.file_name(),"mime":"application/octet-stream","size":fs::metadata(&path).map_err(|e|e.to_string())?.len()}));
        files.insert(handle, path);
    }
    drop(runtime);
    Ok(json!(result))
}
async fn download(state: &AppState, args: &Value) -> Result<Value, String> {
    let expected_hash=args.get("expectedSha256").and_then(Value::as_str);
    if expected_hash.is_some_and(|value|value.len()!=64 || !value.bytes().all(|b|b.is_ascii_digit() || (b'a'..=b'f').contains(&b))) {return Err("下载校验值无效".into());}
    let name = PathBuf::from(text(args,"filename"));
    let name = name.file_name().and_then(|n|n.to_str()).filter(|n|!n.is_empty()).unwrap_or("download");
    let Some(target) = rfd::AsyncFileDialog::new().set_title("保存文件").set_file_name(name).save_file().await else {return Ok(json!({"cancelled":true}));};
    let response = send_request(state, args).await?;
    if !response.status().is_success() || response.headers().get(header::CONTENT_TYPE).and_then(|h|h.to_str().ok()).is_some_and(|t|t.contains("text/html") || t.contains("application/json")) {
        return Err(format!("下载失败，请检查登录状态和权限（{}）", response.status()));
    }
    let temporary = target.path().with_extension(format!("{}.part",uuid::Uuid::new_v4()));
    struct PartialFile(PathBuf);
    impl Drop for PartialFile { fn drop(&mut self) {let _=fs::remove_file(&self.0);} }
    let _cleanup = PartialFile(temporary.clone());
    let result = async {
        let mut file = tokio::fs::File::create(&temporary).await.map_err(|e|e.to_string())?;
        let mut digest=Sha256::new();
        let mut stream = response.bytes_stream();
        while let Some(chunk) = stream.next().await {let chunk=chunk.map_err(|e|e.to_string())?;digest.update(&chunk);file.write_all(&chunk).await.map_err(|e|e.to_string())?;}
        file.flush().await.map_err(|e|e.to_string())?;
        drop(file);
        if expected_hash.is_some_and(|expected|format!("{:x}",digest.finalize())!=expected) {return Err("安装包校验失败，原有文件未更改，请重新下载".into());}
        tokio::fs::rename(&temporary,target.path()).await.map_err(|e|e.to_string())?;
        Ok(json!({"cancelled":false,"path":target.path().to_string_lossy()}))
    }.await;
    if result.is_err() {let _ = tokio::fs::remove_file(&temporary).await;}
    result
}
fn external(app: &tauri::AppHandle, input: &str) -> Result<Value, String> {
    let url = Url::parse(input).map_err(|_|"链接无效")?;
    if !matches!(url.scheme(),"http"|"https") || !url.username().is_empty() || url.password().is_some() {return Err("仅支持在应用内打开 HTTP/HTTPS 网页".into());}
    let id = uuid::Uuid::new_v4().simple().to_string();
    let window = tauri::window::WindowBuilder::new(app,format!("external-{id}"))
        .title("网页浏览 — 巡天御风").inner_size(1100.,800.)
        .visible(smoke_directory().is_none()).build().map_err(|e|e.to_string())?;
    let smoke_profile = smoke_directory().map(|path|path.join("webview"));
    let mut toolbar = tauri::webview::WebviewBuilder::new(format!("external-controls-{id}"),WebviewUrl::App("external.html".into()));
    if let Some(path) = &smoke_profile {toolbar = toolbar.data_directory(path.clone());}
    window.add_child(toolbar,tauri::LogicalPosition::new(0.,0.),tauri::LogicalSize::new(1100.,48.)).map_err(|e|e.to_string())?;
    let mut view = tauri::webview::WebviewBuilder::new(format!("external-page-{id}"),WebviewUrl::External(url))
        .incognito(true)
        .on_navigation(|url|matches!(url.scheme(),"http"|"https"))
        .on_permission_request(|_,_|tauri::webview::PermissionResponse::Deny)
        .on_new_window(|_,_|tauri::webview::NewWindowResponse::Deny);
    if let Some(path) = smoke_profile {view = view.data_directory(path);}
    let remote = window.add_child(view,tauri::LogicalPosition::new(0.,48.),tauri::LogicalSize::new(1100.,752.)).map_err(|e|e.to_string())?;
    let handle = remote.clone();
    window.on_window_event(move |event| {if let tauri::WindowEvent::Resized(size) = event {let _=handle.set_size(tauri::PhysicalSize::new(size.width,size.height.saturating_sub(48)));}});
    Ok(json!({"opened":true}))
}
#[tauri::command]
async fn native_call(app: tauri::AppHandle, webview: tauri::Webview, state: State<'_,AppState>, action: String, args: Value) -> Result<Value,String> {
    if !local_url(&webview.url().map_err(|e|e.to_string())?) {return Err("禁止远程页面调用本地功能".into());}
    if webview.label() == "website-controls" {
        return website::control(&app, &state, &action, &args);
    }
    if webview.label().starts_with("external-controls-") {
        let id = webview.label().trim_start_matches("external-controls-");
        return match action.as_str() {
            "external_back" => {if let Some(view)=app.get_webview(&format!("external-page-{id}")){view.eval("history.back()").map_err(|e|e.to_string())?;} Ok(Value::Null)},
            "external_close" => {webview.window().close().map_err(|e|e.to_string())?;Ok(Value::Null)},
            _=>Err("浏览窗口没有业务权限".into())
        };
    }
    if webview.label() != "main" {return Err("窗口没有业务权限".into());}
    match action.as_str() {
        "get_config"=>{let server=state.server.lock().unwrap().clone();Ok(json!({"serverUrl":server,"effectiveServerUrl":state.effective_server(),"version":env!("CARGO_PKG_VERSION"),"bridgeVersion":2,"capabilities":updates::CAPABILITIES,"platform":updates::platform()}))},
        "probe_connection"=>website::probe(&state).await,
        "open_website"=>{let prepared=website::prepare(&state).await?;website::open(&app, &state, prepared)},
        "return_local"=>website::return_local(&app, text(&args,"intent")),
        "check_updates"=>native_updates::check(&state,args.get("retry").and_then(Value::as_bool).unwrap_or(false)).await,
        "update_status"=>Ok(state.updates.lock().unwrap().last_status.clone()),
        "ui_ready"=>native_updates::ready(&state,text(&args,"viewToken")),
        "apply_update"=>native_updates::apply(&app,&state),
        "set_server"=>{
            let server = normalize_server(text(&args,"serverUrl"))?;
            for token in state.requests.lock().unwrap().values(){token.cancel();}
            state.files.lock().unwrap().clear();
            fs::write(state.directory.join("server.json"),serde_json::to_vec_pretty(&json!({"serverUrl":server})).unwrap()).map_err(|e|e.to_string())?;
            let mut runtime=state.updates.lock().unwrap();
            let mut configured=state.server.lock().unwrap();
            *configured=server.clone();
            *state.effective_server.lock().unwrap()=None;
            runtime.cancellation.cancel();
            *runtime=native_updates::Runtime::new(&state.directory,&server,runtime.generation+1);
            drop(configured);
            drop(runtime);
            website::close(&app);
            native_updates::schedule_reload(&app);
            Ok(Value::Null)
        },
        "pick_files"=>pick_files(&state,&args).await,
        "read_selected_image"=>{
            let path=state.files.lock().unwrap().get(text(&args,"handle")).cloned().ok_or("文件选择已失效，请重新选择")?;
            updates::image_preview(&path)
        },
        "open_external"=>external(&app,text(&args,"url")),
        "cancel"=>{if let Some(token)=state.requests.lock().unwrap().get(text(&args,"requestId")){token.cancel();}Ok(Value::Null)},
        "request"|"read_resource"|"download"=>{
            let id=args.get("requestId").and_then(Value::as_str).map(str::to_string).unwrap_or_else(||uuid::Uuid::new_v4().to_string());
            let token=CancellationToken::new();state.requests.lock().unwrap().insert(id.clone(),token.clone());
            let operation=async {if action=="download" {download(&state,&args).await} else {response_value(send_request(&state,&args).await?,action=="read_resource").await}};
            let result=match futures_util::future::select(operation.boxed(),token.cancelled().boxed()).await {
                futures_util::future::Either::Left((result,_))=>result,
                futures_util::future::Either::Right(_)=>Err("请求已取消；写入结果请刷新核对后再提交".into())
            };
            state.requests.lock().unwrap().remove(&id);result
        },
        _=>Err("未知的本地操作".into())
    }
}
fn legacy_server() -> Option<String> {
    let base = if cfg!(windows) {std::env::var_os("APPDATA").map(PathBuf::from)} else {
        std::env::var_os("XDG_CONFIG_HOME").map(PathBuf::from).or_else(||std::env::var_os("HOME").map(|h|PathBuf::from(h).join(".config")))
    }?;
    ["xtyf-manager-client","巡天御风管理系统"].iter().find_map(|name|{
        let value:Value=serde_json::from_slice(&fs::read(base.join(name).join("server.json")).ok()?).ok()?;
        normalize_server(text(&value,"serverUrl")).ok()
    })
}
fn main() {
    let args:Vec<String>=std::env::args().collect();
    if args.get(1).map(String::as_str)==Some("--build-info") {
        if let Some(output)=args.get(2) {
            let manifest:Value=serde_json::from_str(include_str!("../../frontend/dist/asset-manifest.json")).expect("asset manifest");
            fs::write(output,serde_json::to_vec_pretty(&json!({"version":env!("CARGO_PKG_VERSION"),"framework":"tauri","architecture":std::env::consts::ARCH,"frontend":manifest})).unwrap()).expect("write build information");
        }
        return;
    }
    tauri::Builder::default().invoke_handler(tauri::generate_handler![native_call]).setup(|app|{
        app.remove_menu()?;
        let directory=smoke_directory().unwrap_or(app.path().app_config_dir()?);
        fs::create_dir_all(&directory)?;
        let defaults:Value=serde_json::from_str(include_str!("../../config.json"))?;
        let saved=fs::read(directory.join("server.json")).ok().and_then(|bytes|serde_json::from_slice::<Value>(&bytes).ok()).and_then(|v|normalize_server(text(&v,"serverUrl")).ok());
        let server=saved.or_else(legacy_server).unwrap_or_else(||text(&defaults,"serverUrl").to_string());
        fs::write(directory.join("server.json"),serde_json::to_vec_pretty(&json!({"serverUrl":server}))?)?;
        let runtime=native_updates::Runtime::new(&directory,&server,0);
        app.manage(AppState {server:Mutex::new(server),effective_server:Mutex::new(None),website_epoch:std::sync::atomic::AtomicU64::new(0),website_menu_open:std::sync::atomic::AtomicBool::new(false),connection_gate:tokio::sync::Mutex::new(()),website_files:Mutex::new(std::collections::HashSet::new()),directory,sessions:Mutex::new(HashMap::new()),files:Mutex::new(HashMap::new()),requests:Mutex::new(HashMap::new()),updates:Mutex::new(runtime),update_gate:tokio::sync::Mutex::new(())});
        let window=tauri::window::WindowBuilder::new(app,"workspace")
            .title("巡天御风管理系统").inner_size(1380.,900.).min_inner_size(360.,600.)
            .visible(smoke_directory().is_none())
            .build()?;
        let handle=app.handle().clone();
        window.on_window_event(move |event| {if let tauri::WindowEvent::Resized(size)=event {if let Some(view)=handle.get_webview("main"){let _=view.set_size(*size);}website::resize(&handle,*size);}});
        native_updates::create_view(app.handle()).map_err(std::io::Error::other)?;
        Ok(())
    }).run(tauri::generate_context!()).expect("客户端启动失败");
}

#[cfg(test)]
mod network_tests;

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn server_validation(){
        assert_eq!(normalize_server(" http://127.0.0.1:8520/ ").unwrap(),"http://127.0.0.1:8520");
        for input in ["file:///a","javascript:alert(1)","https://u:p@example.com","https://example.com/a","https://example.com/?a=b","https://example.com/#x",""]{assert!(normalize_server(input).is_err(),"{input}");}
    }
    #[test] fn default_server_preserves_http(){
        let defaults:Value=serde_json::from_str(include_str!("../../config.json")).unwrap();
        assert_eq!(text(&defaults,"serverUrl"), "http://manager.skywalker-rm.online");
        for input in ["http://manager.skywalker-rm.online", " http://MANAGER.skywalker-rm.online:80/ "] {
            let server=normalize_server(input).unwrap();
            assert_eq!(server, "http://manager.skywalker-rm.online");
            assert_eq!(server_path(&server, "/api/client/bootstrap").unwrap().as_str(), "http://manager.skywalker-rm.online/api/client/bootstrap");
        }
        for input in ["https://manager.skywalker-rm.online", "http://manager.skywalker-rm.online:8080", "http://127.0.0.1:8520", "http://example.com", "http://manager.skywalker-rm.online.example.com"] {
            assert_eq!(normalize_server(input).unwrap(), input);
        }
    }
    #[test] fn request_origin(){
        assert!(server_path("http://example.com","/api/data").is_ok());
        for path in ["//elsewhere/api","https://elsewhere/api","/\\elsewhere/api"]{assert!(server_path("http://example.com",path).is_err());}
    }
    #[test] fn bridge_origin(){
        assert!(local_url(&Url::parse("http://tauri.localhost/index.html").unwrap()));
        assert!(!local_url(&Url::parse("https://example.com/index.html").unwrap()));
        for url in ["http://tauri.localhost:9123/index.html", "https://user@tauri.localhost/", "tauri://user:password@localhost/", "tauri://localhost:123/"] {
            assert!(!local_url(&Url::parse(url).unwrap()), "{url}");
        }
    }
}
