//! Signed, immutable UI bundles. Network I/O never changes a running view's root.
use base64::{engine::general_purpose::STANDARD, Engine};
use futures_util::StreamExt;
use ring::signature::{RsaPublicKeyComponents, RSA_PKCS1_2048_8192_SHA256};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{collections::{BTreeMap, HashSet}, fs, io::{Read, Write}, path::{Path, PathBuf}};

pub const CAPABILITIES: &[&str] = &["ui_updates", "image_preview"];
pub const METADATA_LIMIT: u64 = 256 * 1024;
pub const ZIP_LIMIT: u64 = 128 * 1024 * 1024;
pub const UNPACKED_LIMIT: u64 = 256 * 1024 * 1024;
pub const FILE_LIMIT: u64 = 4096;
const KEY: &str = include_str!("../../update-public-key.pem");
const REQUIRED: &[&str] = &["index.html", "shell.js", "bridge.js", "page-runtime.js", "templates.js"];
pub fn hash(bytes: &[u8]) -> String { format!("{:x}", Sha256::digest(bytes)) }
fn valid_hash(value: &str) -> bool { value.len() == 64 && value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) }
fn invalid(message: &str) -> String { format!("页面更新校验失败：{message}") }
pub fn safe_path(value: &str) -> bool {
    !value.is_empty() && !value.starts_with('/') && !value.contains(['\\', ':', '\0'])
        && value.split('/').all(|part| !part.is_empty() && !matches!(part, "." | "..")
            && !part.ends_with(['.', ' ']) && !part.chars().any(char::is_control)
            && !matches!(part.split('.').next().unwrap_or("").to_uppercase().as_str(),
                "CON" | "PRN" | "AUX" | "NUL" | "COM1" | "COM2" | "COM3" | "COM4" | "COM5" | "COM6" | "COM7" | "COM8" | "COM9" | "LPT1" | "LPT2" | "LPT3" | "LPT4" | "LPT5" | "LPT6" | "LPT7" | "LPT8" | "LPT9"))
}
fn safe_url(value: &str) -> bool {
    value.starts_with('/') && !value.starts_with("//") && !value.contains(['\\', '#'])
        && !value.chars().any(char::is_control)
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Bundle {
    pub id: String, pub version: String, pub url: String, pub size: u64, pub sha256: String,
    pub unpacked_size: u64, pub file_count: u64, pub protocol_min: u64, pub protocol_max: u64,
    pub bridge_min: u64, pub min_client_version: String, pub capabilities: Vec<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Installer { pub version: String, pub url: String, pub sha256: String }
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Release { pub format: u64, pub sequence: u64, pub bundle: Bundle, pub installers: BTreeMap<String, Installer> }
#[derive(Deserialize)]
struct Envelope { ok: bool, available: bool, payload: Option<String>, signature: Option<String> }
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Manifest { protocol_version: u64, files: BTreeMap<String, String>, sizes: BTreeMap<String, u64> }

// The pinned key is SPKI DER. Decode its two unsigned RSA integers; no key is ever
// accepted from a release response. Strict tags and full consumption avoid ambiguity.
fn der<'a>(bytes: &mut &'a [u8], tag: u8) -> Result<&'a [u8], String> {
    if bytes.len() < 2 || bytes[0] != tag { return Err(invalid("公钥格式")); }
    let mut count = 2; let mut length = bytes[1] as usize;
    if length & 128 != 0 {
        let octets = length & 127;
        if octets == 0 || octets > 4 || bytes.len() < count + octets { return Err(invalid("公钥长度")); }
        length = 0;
        for byte in &bytes[count..count + octets] { length = (length << 8) | *byte as usize; }
        count += octets;
    }
    if length > bytes.len() - count { return Err(invalid("公钥长度")); }
    let value = &bytes[count..count + length]; *bytes = &bytes[count + length..]; Ok(value)
}
fn verify_key(pem: &str, payload: &[u8], signature: &[u8]) -> Result<(), String> {
    let encoded: String = pem.lines().filter(|s| !s.starts_with("-----")).collect();
    let bytes = STANDARD.decode(encoded).map_err(|_| invalid("公钥编码"))?;
    let mut source = bytes.as_slice(); let mut spki = der(&mut source, 0x30)?;
    if !source.is_empty() { return Err(invalid("公钥尾部")); }
    let algorithm = der(&mut spki, 0x30)?;
    if algorithm != [0x06,0x09,0x2a,0x86,0x48,0x86,0xf7,0x0d,0x01,0x01,0x01,0x05,0x00] { return Err(invalid("公钥算法")); }
    let bitstring = der(&mut spki, 0x03)?;
    if !spki.is_empty() || bitstring.first() != Some(&0) { return Err(invalid("公钥位串")); }
    let mut rsa = &bitstring[1..]; let mut numbers = der(&mut rsa, 0x30)?;
    if !rsa.is_empty() { return Err(invalid("公钥尾部")); }
    let n = der(&mut numbers, 0x02)?; let n = n.strip_prefix(&[0]).unwrap_or(n);
    let e = der(&mut numbers, 0x02)?;
    if !numbers.is_empty() || n.len() != 384 || n[0] & 128 == 0 || signature.len() != 384 { return Err(invalid("需要 RSA-3072 签名")); }
    RsaPublicKeyComponents { n, e }.verify(&RSA_PKCS1_2048_8192_SHA256, payload, signature).map_err(|_| invalid("签名不正确"))
}
pub fn parse_release(bytes: &[u8]) -> Result<Option<(Release, String)>, String> {
    #[cfg(debug_assertions)]
    if crate::smoke_directory().is_some() {
        if let Some(path)=std::env::var_os("XTYF_UI_TEST_PUBLIC_KEY") {
            let key=fs::read_to_string(path).map_err(|e|e.to_string())?;
            return parse_release_with_key(bytes,&key);
        }
    }
    parse_release_with_key(bytes, KEY)
}
pub(crate) fn parse_release_with_key(bytes: &[u8], key: &str) -> Result<Option<(Release, String)>, String> {
    if bytes.len() as u64 > METADATA_LIMIT { return Err(invalid("清单过大")); }
    let envelope: Envelope = serde_json::from_slice(bytes).map_err(|_| invalid("清单格式"))?;
    if !envelope.ok { return Err(invalid("清单不可用")); }
    if !envelope.available { return Ok(None); }
    let payload = STANDARD.decode(envelope.payload.ok_or_else(|| invalid("清单缺少正文"))?).map_err(|_| invalid("正文编码"))?;
    let signature = STANDARD.decode(envelope.signature.ok_or_else(|| invalid("清单缺少签名"))?).map_err(|_| invalid("签名编码"))?;
    verify_key(key, &payload, &signature)?;
    let release: Release = serde_json::from_slice(&payload).map_err(|_| invalid("正文格式"))?;
    let bundle = &release.bundle;
    if release.format != 1 || release.sequence == 0 || release.sequence > 9_007_199_254_740_991
        || !valid_hash(&bundle.id) || !valid_hash(&bundle.sha256) || !safe_url(&bundle.url)
        || bundle.url != format!("/api/client/ui/{}.zip", bundle.id) || bundle.size == 0 || bundle.size > ZIP_LIMIT
        || bundle.unpacked_size == 0 || bundle.unpacked_size > UNPACKED_LIMIT || bundle.file_count == 0 || bundle.file_count > FILE_LIMIT
        || bundle.protocol_min > bundle.protocol_max || bundle.version.is_empty()
        || release.installers.values().any(|i| !safe_url(&i.url) || !valid_hash(&i.sha256) || version(&i.version).is_none()) {
        return Err(invalid("清单字段"));
    }
    Ok(Some((release, hash(&payload))))
}
fn version(value: &str) -> Option<[u64; 3]> {
    let parts: Vec<_> = value.split('.').collect();
    if parts.len() != 3 || parts.iter().any(|s| s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit())) { return None; }
    Some([parts[0].parse().ok()?, parts[1].parse().ok()?, parts[2].parse().ok()?])
}
pub fn compatibility(bundle: &Bundle) -> Result<(), String> {
    if !(bundle.protocol_min..=bundle.protocol_max).contains(&1) { return Err("业务协议不兼容，请升级客户端".into()); }
    if bundle.bridge_min > 2 || bundle.capabilities.iter().any(|c| !CAPABILITIES.contains(&c.as_str())) { return Err("新版页面需要新的原生能力，请升级客户端".into()); }
    let minimum = version(&bundle.min_client_version).ok_or_else(|| invalid("最低客户端版本"))?;
    if minimum > version(env!("CARGO_PKG_VERSION")).unwrap() { return Err(format!("请先升级客户端至 {} 或更高版本", bundle.min_client_version)); }
    Ok(())
}
pub fn platform() -> &'static str {
    if cfg!(target_os = "windows") { "windows-x86_64" } else if cfg!(target_arch = "aarch64") { "ubuntu-aarch64" } else { "fedora-x86_64" }
}
pub async fn bounded_response(response: reqwest::Response, limit: u64) -> Result<Vec<u8>, String> {
    if !response.status().is_success() { return Err(format!("更新请求失败（{}）", response.status())); }
    if response.content_length().is_some_and(|n| n > limit) { return Err(invalid("下载超过大小限制")); }
    let mut stream = response.bytes_stream(); let mut bytes = Vec::new();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| e.to_string())?;
        if bytes.len() as u64 + chunk.len() as u64 > limit { return Err(invalid("下载超过大小限制")); }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}
pub fn extract(bytes: &[u8], bundle: &Bundle, root: &Path) -> Result<(), String> {
    if bytes.len() as u64 != bundle.size || hash(bytes) != bundle.sha256 { return Err(invalid("资源包大小或哈希不符")); }
    let mut archive = zip::ZipArchive::new(std::io::Cursor::new(bytes)).map_err(|e| invalid(&e.to_string()))?;
    if archive.len() as u64 != bundle.file_count || archive.len() as u64 > FILE_LIMIT { return Err(invalid("文件数量不符")); }
    let mut names = HashSet::new(); let mut total = 0_u64;
    for index in 0..archive.len() {
        let file = archive.by_index(index).map_err(|e| invalid(&e.to_string()))?;
        if !safe_path(file.name()) || file.is_dir() || !names.insert(file.name().to_lowercase())
            || file.unix_mode().is_some_and(|mode| mode & 0o170000 != 0 && mode & 0o170000 != 0o100000) {
            return Err(invalid("资源包路径、重复项或链接"));
        }
        total = total.checked_add(file.size()).ok_or_else(|| invalid("文件大小溢出"))?;
        if total > bundle.unpacked_size || total > UNPACKED_LIMIT { return Err(invalid("解压大小超限")); }
    }
    if total != bundle.unpacked_size { return Err(invalid("解压大小不符")); }
    let mut manifest_bytes = Vec::new();
    archive.by_name("asset-manifest.json").map_err(|_| invalid("缺少资源清单"))?.take(METADATA_LIMIT + 1).read_to_end(&mut manifest_bytes).map_err(|e| e.to_string())?;
    if manifest_bytes.len() as u64 > METADATA_LIMIT || hash(&manifest_bytes) != bundle.id { return Err(invalid("资源清单哈希不符")); }
    let manifest: Manifest = serde_json::from_slice(&manifest_bytes).map_err(|_| invalid("资源清单格式"))?;
    if manifest.protocol_version != 1 || manifest.files.len() + 1 != archive.len() || manifest.sizes.len() != manifest.files.len()
        || REQUIRED.iter().any(|p| !manifest.files.contains_key(*p)) || manifest.files.contains_key("asset-manifest.json") {
        return Err(invalid("缺少必需资源或多余文件"));
    }
    fs::create_dir_all(root).map_err(|e| e.to_string())?;
    for index in 0..archive.len() {
        let mut file = archive.by_index(index).map_err(|e| invalid(&e.to_string()))?;
        let name = file.name().to_string();
        let (expected_size, expected_hash) = if name == "asset-manifest.json" {(manifest_bytes.len() as u64, bundle.id.as_str())} else {
            (*manifest.sizes.get(&name).ok_or_else(|| invalid("缺少文件大小"))?, manifest.files.get(&name).ok_or_else(|| invalid("未列入清单的文件"))?.as_str())
        };
        if file.size() != expected_size || !valid_hash(expected_hash) { return Err(invalid("文件大小不符")); }
        let destination = root.join(&name); fs::create_dir_all(destination.parent().unwrap()).map_err(|e| e.to_string())?;
        let mut output = fs::OpenOptions::new().create_new(true).write(true).open(destination).map_err(|e| e.to_string())?;
        let mut actual = 0_u64; let mut digest = Sha256::new(); let mut buffer = [0_u8; 65536];
        loop {
            let size = file.read(&mut buffer).map_err(|e| invalid(&e.to_string()))?; if size == 0 { break; }
            actual += size as u64; if actual > expected_size { return Err(invalid("实际解压大小超限")); }
            digest.update(&buffer[..size]); output.write_all(&buffer[..size]).map_err(|e| e.to_string())?;
        }
        if actual != expected_size || format!("{:x}", digest.finalize()) != expected_hash { return Err(invalid("文件哈希不符")); }
        output.sync_all().map_err(|e| e.to_string())?;
    }
    Ok(())
}

pub fn validate_root(root: &Path, bundle: &Bundle) -> Result<(), String> {
    let manifest_bytes = fs::read(root.join("asset-manifest.json")).map_err(|e|e.to_string())?;
    if manifest_bytes.len() as u64 > METADATA_LIMIT || hash(&manifest_bytes) != bundle.id { return Err(invalid("缓存清单已损坏")); }
    let manifest: Manifest = serde_json::from_slice(&manifest_bytes).map_err(|_|invalid("缓存清单格式"))?;
    if manifest.protocol_version != 1 || manifest.files.len() as u64 + 1 != bundle.file_count || manifest.files.len() != manifest.sizes.len()
        || REQUIRED.iter().any(|p| !manifest.files.contains_key(*p)) || manifest.files.contains_key("asset-manifest.json") { return Err(invalid("缓存清单不完整")); }
    let mut total = manifest_bytes.len() as u64;
    let mut paths = HashSet::new();
    for (name, digest) in &manifest.files {
        if !safe_path(name) || !paths.insert(name.to_lowercase()) { return Err(invalid("缓存路径无效")); }
        let mut path = root.to_path_buf();
        for part in name.split('/') {
            path.push(part);
            if fs::symlink_metadata(&path).map_err(|e|e.to_string())?.file_type().is_symlink() { return Err(invalid("缓存中不允许链接")); }
        }
        let expected = *manifest.sizes.get(name).ok_or_else(||invalid("缓存缺少大小"))?;
        total = total.checked_add(expected).ok_or_else(||invalid("缓存大小溢出"))?;
        if total > bundle.unpacked_size || total > UNPACKED_LIMIT { return Err(invalid("缓存大小超限")); }
        let mut file = fs::File::open(path).map_err(|e|e.to_string())?;
        if file.metadata().map_err(|e|e.to_string())?.len() != expected { return Err(invalid("缓存大小不符")); }
        let mut actual = 0_u64; let mut calculated = Sha256::new(); let mut buffer = [0_u8;65536];
        loop { let count = file.read(&mut buffer).map_err(|e|e.to_string())?; if count == 0 { break; } actual += count as u64; if actual > expected { return Err(invalid("缓存大小变化")); } calculated.update(&buffer[..count]); }
        if actual != expected || format!("{:x}",calculated.finalize()) != *digest { return Err(invalid("缓存资源已损坏")); }
    }
    if total != bundle.unpacked_size { return Err(invalid("缓存总大小不符")); }
    Ok(())
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct DiskState {
    pub highest: u64, pub payload_hash: String, pub current: Option<Bundle>, pub previous: Option<Bundle>, pub pending: Option<Bundle>,
    pub trial: Option<Bundle>, pub quarantined: Vec<String>, pub installer: Option<Installer>,
}
pub struct Store { pub directory: PathBuf, pub state: DiskState }
impl Store {
    pub fn open(directory: &Path, server: &str) -> Result<Self, String> {
        let directory = directory.join("ui-updates").join(hash(server.as_bytes()));
        fs::create_dir_all(directory.join("bundles")).map_err(|e| e.to_string())?;
        let state = match fs::read(directory.join("state.json")) {
            Ok(bytes) => serde_json::from_slice::<DiskState>(&bytes).map_err(|_| invalid("本地更新状态损坏，保留数据并停止更新"))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => DiskState::default(), Err(e) => return Err(e.to_string()),
        };
        if [&state.current, &state.previous, &state.pending, &state.trial].iter().filter_map(|b|b.as_ref()).any(|b| !valid_hash(&b.id)) { return Err(invalid("本地资源标识")); }
        Ok(Self { directory, state })
    }
    fn save(&self) -> Result<(), String> {
        let mut file = tempfile::NamedTempFile::new_in(&self.directory).map_err(|e| e.to_string())?;
        serde_json::to_writer(&mut file, &self.state).map_err(|e| e.to_string())?;
        file.as_file().sync_all().map_err(|e| e.to_string())?;
        file.persist(self.directory.join("state.json")).map_err(|e| e.to_string())?; Ok(())
    }
    fn change(&mut self, change: impl FnOnce(&mut DiskState)) -> Result<(), String> {
        let before = self.state.clone(); change(&mut self.state);
        if let Err(error) = self.save() { self.state = before; return Err(error); } Ok(())
    }
    pub fn accept(&mut self, release: &Release, payload_hash: &str) -> Result<(), String> {
        if release.sequence < self.state.highest || (release.sequence == self.state.highest && self.state.payload_hash != payload_hash) { return Err(invalid("发布序号已过期或相同序号内容变化")); }
        self.change(|state| { state.highest = release.sequence; state.payload_hash = payload_hash.into(); state.installer = release.installers.get(platform()).cloned();
            if compatibility(&release.bundle).is_err() || state.pending.as_ref().is_some_and(|b| b.id != release.bundle.id) { state.pending = None; }
        })
    }
    pub fn root(&self, bundle: &Bundle) -> PathBuf { self.directory.join("bundles").join(&bundle.id) }
    pub fn available(&self, bundle: &Bundle) -> bool { validate_root(&self.root(bundle), bundle).is_ok() }
    pub fn stage(&mut self, bundle: &Bundle, bytes: &[u8]) -> Result<(), String> {
        let temporary = tempfile::Builder::new().prefix("staging-").tempdir_in(&self.directory).map_err(|e| e.to_string())?;
        extract(bytes, bundle, temporary.path())?;
        let destination = self.root(bundle);
        if destination.exists() && !self.available(bundle) {
            if [&self.state.current,&self.state.previous,&self.state.trial].iter().filter_map(|b|b.as_ref()).any(|b|b.id==bundle.id) { return Err(invalid("在用页面缓存损坏，保留当前文件并等待回退")); }
            let discarded = self.directory.join(format!("corrupt-{}",uuid::Uuid::new_v4()));
            fs::rename(&destination,&discarded).map_err(|e|e.to_string())?;
            if let Err(error)=fs::rename(temporary.path(),&destination) { let _=fs::rename(&discarded,&destination); return Err(error.to_string()); }
            let _=fs::remove_dir_all(discarded);
        } else if !destination.exists() { fs::rename(temporary.path(), &destination).map_err(|e| e.to_string())?; }
        self.change(|state| state.pending = Some(bundle.clone()))
    }
    pub fn launch(&mut self, retry: bool) -> Result<Option<Bundle>, String> {
        // An unconfirmed trial survived a process crash: quarantine it before selecting anything.
        if self.state.trial.is_some() { self.failed()?; }
        if let Some(pending) = self.state.pending.clone() {
            if self.available(&pending) && (retry || !self.state.quarantined.contains(&pending.id)) {
                self.change(|state| { state.pending = None; state.trial = Some(pending.clone()); })?;
                return Ok(Some(pending));
            }
            self.change(|state| { state.pending = None; if !state.quarantined.contains(&pending.id) { state.quarantined.push(pending.id); } })?;
        }
        if let Some(current) = self.state.current.clone().filter(|b| self.available(b) && !self.state.quarantined.contains(&b.id)) {
            self.change(|state| state.trial = Some(current.clone()))?;
            return Ok(Some(current));
        }
        if let Some(current) = self.state.current.clone() {
            self.change(|state| { if !state.quarantined.contains(&current.id) { state.quarantined.push(current.id); } state.current = state.previous.take(); })?;
            if let Some(previous) = self.state.current.clone().filter(|b| self.available(b) && !self.state.quarantined.contains(&b.id)) {
                self.change(|state| state.trial = Some(previous.clone()))?; return Ok(Some(previous));
            }
            self.change(|state| state.current = None)?;
        }
        Ok(None)
    }
    pub fn ready(&mut self, id: Option<&str>) -> Result<(), String> {
        if let Some(trial) = self.state.trial.clone().filter(|b| Some(b.id.as_str()) == id) {
            self.change(|state| {
                if state.current.as_ref().is_none_or(|c| c.id != trial.id) { state.previous = state.current.take(); state.current = Some(trial.clone()); }
                state.trial = None; state.quarantined.retain(|value| value != &trial.id);
            })?;
        }
        self.cleanup(); Ok(())
    }
    pub fn failed(&mut self) -> Result<Option<Bundle>, String> {
        let trial = self.state.trial.clone();
        self.change(|state| {
            if let Some(trial) = &trial {
                if !state.quarantined.contains(&trial.id) { state.quarantined.push(trial.id.clone()); }
                if state.current.as_ref().is_some_and(|current| current.id == trial.id) { state.current = state.previous.take(); }
            }
            state.trial = None;
        })?;
        Ok(self.state.current.clone().filter(|b| self.available(b) && !self.state.quarantined.contains(&b.id)))
    }
    pub fn retry(&mut self, id: &str) -> Result<(), String> { self.change(|state| state.quarantined.retain(|value| value != id)) }
    pub fn begin_trial(&mut self, bundle: Option<Bundle>) -> Result<(), String> { self.change(|state| state.trial = bundle) }
    pub fn cleanup(&self) {
        let keep: HashSet<_> = [&self.state.current, &self.state.previous, &self.state.pending, &self.state.trial].iter().filter_map(|b| b.as_ref().map(|b| b.id.as_str())).collect();
        if let Ok(entries) = fs::read_dir(self.directory.join("bundles")) {
            for entry in entries.flatten() {
                if let Some(name) = entry.file_name().to_str() { if valid_hash(name) && !keep.contains(name) && entry.file_type().is_ok_and(|t| t.is_dir()) { let _ = fs::remove_dir_all(entry.path()); } }
            }
        }
    }
    pub fn status(&self, status: &str, message: &str, active: Option<&Bundle>) -> Value {
        json!({"status":status,"message":message,"currentVersion":active.map(|b|b.version.as_str()).unwrap_or(env!("CARGO_PKG_VERSION")),"pendingVersion":self.state.pending.as_ref().map(|b|&b.version),"installer":self.state.installer})
    }
}

pub fn image_preview(path: &Path) -> Result<Value, String> {
    let file = fs::File::open(path).map_err(|e| e.to_string())?;
    const LIMIT: u64 = 10 * 1024 * 1024;
    if file.metadata().map_err(|e| e.to_string())?.len() > LIMIT { return Err("图片不能超过 10 MiB".into()); }
    let mut bytes = Vec::new(); file.take(LIMIT + 1).read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    if bytes.len() as u64 > LIMIT { return Err("图片不能超过 10 MiB".into()); }
    let mime = if bytes.starts_with(b"\x89PNG\r\n\x1a\n") { "image/png" }
        else if bytes.starts_with(&[0xff,0xd8,0xff]) { "image/jpeg" }
        else if bytes.starts_with(b"RIFF") && bytes.get(8..12) == Some(b"WEBP") { "image/webp" }
        else { return Err("请选择 JPEG、PNG 或 WebP 图片".into()); };
    Ok(json!({"body":STANDARD.encode(&bytes),"mime":mime,"size":bytes.len()}))
}

#[cfg(test)] mod tests;
