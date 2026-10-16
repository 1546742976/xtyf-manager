use super::*;
use std::io::{BufRead, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::thread::{self, JoinHandle};

#[derive(Default)]
struct Incoming { method: String, path: String, headers: HashMap<String, String>, body: Vec<u8> }
struct Outgoing { status: u16, headers: Vec<(String, String)>, body: Vec<u8> }
impl Outgoing {
    fn text(status: u16, value: &str) -> Self { Self { status, headers: vec![], body: value.as_bytes().to_vec() } }
    fn with(mut self, name: &str, value: &str) -> Self { self.headers.push((name.into(), value.into())); self }
}
struct LocalServer { origin: String, stopped: Arc<AtomicBool>, worker: Option<JoinHandle<()>> }
impl LocalServer {
    fn start(handler: impl Fn(Incoming) -> Outgoing + Send + 'static) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let origin = format!("http://{}", listener.local_addr().unwrap());
        listener.set_nonblocking(true).unwrap();
        let stopped = Arc::new(AtomicBool::new(false)); let stop = stopped.clone();
        let worker = thread::spawn(move || {
            while !stop.load(Ordering::SeqCst) {
                let (mut stream, _) = match listener.accept() {
                    Ok(value) => value,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => { thread::sleep(Duration::from_millis(5)); continue; },
                    Err(error) => panic!("accept: {error}"),
                };
                stream.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let mut line = String::new(); if reader.read_line(&mut line).unwrap_or(0) == 0 { continue; }
                let mut parts = line.split_whitespace();
                let mut incoming = Incoming { method: parts.next().unwrap().into(), path: parts.next().unwrap().into(), ..Default::default() };
                loop {
                    line.clear(); reader.read_line(&mut line).unwrap(); if line == "\r\n" || line.is_empty() { break; }
                    let (key, value) = line.split_once(':').unwrap(); incoming.headers.insert(key.to_lowercase(), value.trim().into());
                }
                if let Some(length) = incoming.headers.get("content-length") {
                    incoming.body.resize(length.parse().unwrap(), 0); reader.read_exact(&mut incoming.body).unwrap();
                } else if incoming.headers.get("transfer-encoding").is_some_and(|v| v == "chunked") {
                    loop {
                        line.clear(); reader.read_line(&mut line).unwrap(); let size = usize::from_str_radix(line.trim(), 16).unwrap();
                        if size == 0 { break; }
                        let start = incoming.body.len(); incoming.body.resize(start + size, 0); reader.read_exact(&mut incoming.body[start..]).unwrap();
                        let mut delimiter = [0; 2]; reader.read_exact(&mut delimiter).unwrap();
                    }
                }
                let response = handler(incoming);
                let mut head = format!("HTTP/1.1 {} Test\r\nConnection: close\r\nContent-Length: {}\r\n", response.status, response.body.len());
                for (name, value) in response.headers { head += &format!("{name}: {value}\r\n"); }
                head += "\r\n"; stream.write_all(head.as_bytes()).unwrap(); stream.write_all(&response.body).unwrap();
            }
        });
        Self { origin, stopped, worker: Some(worker) }
    }
}
impl Drop for LocalServer {
    fn drop(&mut self) { self.stopped.store(true, Ordering::SeqCst); let _ = TcpStream::connect(self.origin.trim_start_matches("http://")); if let Some(worker) = self.worker.take() { worker.join().unwrap(); } }
}
struct Scratch(PathBuf);
impl Scratch { fn new() -> Self { let path = std::env::temp_dir().join(format!("xtyf-native-http-{}", uuid::Uuid::new_v4())); fs::create_dir(&path).unwrap(); Self(path) } }
impl Drop for Scratch {
    fn drop(&mut self) { assert_eq!(self.0.parent(), Some(std::env::temp_dir().as_path())); let _ = fs::remove_dir_all(&self.0); }
}
fn state(server: &str, directory: &std::path::Path) -> AppState {
    AppState { server: Mutex::new(server.into()), directory: directory.into(), sessions: Mutex::new(HashMap::new()), files: Mutex::new(HashMap::new()), requests: Mutex::new(HashMap::new()) }
}
async fn call(state: &AppState, args: Value) -> Value { response_value(send_request(state, &args).await.unwrap(), false).await.unwrap() }
fn echo(request: Incoming) -> Outgoing {
    Outgoing::text(200, &json!({"method":request.method,"headers":request.headers,"body":String::from_utf8_lossy(&request.body)}).to_string()).with("Content-Type", "application/json")
}
fn response_json(value: &Value) -> Value { serde_json::from_str(value["body"].as_str().unwrap()).unwrap() }

#[test]
fn cookies_csrf_rotation_persistence_and_header_boundary() {
    let server = LocalServer::start(|request| match request.path.as_str() {
        "/bootstrap" => Outgoing::text(200, "boot").with("Set-Cookie", "session=synthetic-login; Path=/; Max-Age=3600; HttpOnly").with("X-CSRF-Token", "token-1"),
        "/rotate" => Outgoing::text(200, "rotated").with("X-CSRF-Token", "token-2"),
        "/api/csrf" => {
            assert_eq!(request.headers.get("x-xtyf-client-protocol").map(String::as_str), Some("1"));
            Outgoing::text(200, "{}").with("X-CSRF-Token", "token-after-restart")
        },
        _ => echo(request),
    });
    let scratch = Scratch::new(); let first = state(&server.origin, &scratch.0);
    tauri::async_runtime::block_on(async {
        let bootstrap = call(&first, json!({"path":"/bootstrap"})).await;
        assert!(bootstrap["headers"].get("set-cookie").is_none(), "Cookie credentials must not be returned to the page");
        call(&first, json!({"path":"/rotate"})).await;
        let request = json!({"path":"/echo","method":"POST","body":"test-body","headers":{"Cookie":"spoof","Authorization":"spoof","X-CSRF-Token":"spoof","X-XTYF-Client-Protocol":"spoof","Content-Type":"text/plain"}});
        let echoed = response_json(&call(&first, request.clone()).await);
        assert_eq!(echoed["body"], "test-body"); assert_eq!(echoed["headers"]["cookie"], "session=synthetic-login");
        assert_eq!(echoed["headers"]["x-csrf-token"], "token-2"); assert_eq!(echoed["headers"]["x-xtyf-client-protocol"], "1");
        assert!(echoed["headers"].get("authorization").is_none());
        let restarted = state(&server.origin, &scratch.0);
        let echoed = response_json(&call(&restarted, request).await);
        assert_eq!(echoed["headers"]["cookie"], "session=synthetic-login"); assert_eq!(echoed["headers"]["x-csrf-token"], "token-after-restart");
    });
}

#[test]
fn redirect_boundary_and_write_not_replayed() {
    let outside_hits = Arc::new(AtomicUsize::new(0)); let hits = outside_hits.clone();
    let outside = LocalServer::start(move |_| { hits.fetch_add(1, Ordering::SeqCst); Outgoing::text(200, "outside") });
    let outside_origin = outside.origin.clone(); let writes = Arc::new(AtomicUsize::new(0)); let write_hits = writes.clone();
    let server = LocalServer::start(move |request| match request.path.as_str() {
        "/api/csrf" => Outgoing::text(200, "{}").with("X-CSRF-Token", "token"),
        "/same" => Outgoing::text(302, "redirect").with("Location", "/echo"),
        "/outside" => Outgoing::text(302, "redirect").with("Location", &outside_origin),
        "/write" => { write_hits.fetch_add(1, Ordering::SeqCst); Outgoing::text(307, "redirect").with("Location", "/written") },
        "/written" => { write_hits.fetch_add(100, Ordering::SeqCst); Outgoing::text(200, "written") },
        _ => echo(request),
    });
    let scratch = Scratch::new(); let app = state(&server.origin, &scratch.0);
    tauri::async_runtime::block_on(async {
        assert_eq!(response_json(&call(&app, json!({"path":"/same"})).await)["method"], "GET");
        assert!(send_request(&app, &json!({"path":"/outside"})).await.is_err()); assert_eq!(outside_hits.load(Ordering::SeqCst), 0);
        assert!(send_request(&app, &json!({"path":"/write","method":"POST","body":"{}"})).await.is_err()); assert_eq!(writes.load(Ordering::SeqCst), 1);
    });
}

#[test]
fn server_sessions_isolated_and_binary_resource_preserved() {
    let first = LocalServer::start(|request| if request.path == "/bootstrap" { Outgoing::text(200, "ok").with("Set-Cookie", "session=first; Path=/; Max-Age=3600") } else { echo(request) });
    let second = LocalServer::start(|request| if request.path == "/bytes" { Outgoing { status: 200, headers: vec![], body: vec![0, 255, 127, 128, 13, 10] } } else { echo(request) });
    let scratch = Scratch::new(); let app = state(&first.origin, &scratch.0);
    tauri::async_runtime::block_on(async {
        call(&app, json!({"path":"/bootstrap"})).await;
        *app.server.lock().unwrap() = second.origin.clone();
        assert!(response_json(&call(&app, json!({"path":"/echo"})).await)["headers"].get("cookie").is_none());
        let bytes = response_value(send_request(&app, &json!({"path":"/bytes"})).await.unwrap(), true).await.unwrap();
        assert_eq!(STANDARD.decode(bytes["body"].as_str().unwrap()).unwrap(), [0, 255, 127, 128, 13, 10]);
        *app.server.lock().unwrap() = first.origin.clone();
        assert_eq!(response_json(&call(&app, json!({"path":"/echo"})).await)["headers"]["cookie"], "session=first");
    });
}

#[test]
fn multipart_uses_selected_handle_and_streams_bytes() {
    let server = LocalServer::start(|request| if request.path == "/api/csrf" { Outgoing::text(200, "{}").with("X-CSRF-Token", "token") } else { echo(request) });
    let scratch = Scratch::new(); let app = state(&server.origin, &scratch.0); let path = scratch.0.join("selected.txt"); fs::write(&path, "selected file content").unwrap();
    app.files.lock().unwrap().insert("selected-handle".into(), path);
    tauri::async_runtime::block_on(async {
        let echoed = response_json(&call(&app, json!({"path":"/upload","method":"POST","multipart":{"fields":[{"name":"note","value":"test note"}],"files":[{"field":"file","handle":"selected-handle","name":"ignored-client-name","mime":"text/plain"}]}})).await);
        let body = echoed["body"].as_str().unwrap();
        assert!(body.contains("name=\"note\"")); assert!(body.contains("test note")); assert!(body.contains("filename=\"selected.txt\"")); assert!(body.contains("selected file content"));
        assert!(send_request(&app, &json!({"path":"/upload","method":"POST","multipart":{"files":[{"field":"file","handle":"missing"}]}})).await.is_err());
    });
}
