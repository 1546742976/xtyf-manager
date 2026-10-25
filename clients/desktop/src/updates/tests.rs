use super::*;
use std::io::Cursor;
use zip::{write::FileOptions, ZipWriter};

fn fixture(version: &str) -> (Bundle, Vec<u8>) {
    let files: BTreeMap<String, Vec<u8>> = REQUIRED.iter().map(|name|(name.to_string(),format!("synthetic {version} {name}").into_bytes())).collect();
    let manifest = json!({"protocolVersion":1,"version":"1.0.10","templates":[],
        "files":files.iter().map(|(k,v)|(k.clone(),hash(v))).collect::<BTreeMap<_,_>>(),
        "sizes":files.iter().map(|(k,v)|(k.clone(),v.len())).collect::<BTreeMap<_,_>>()});
    let manifest_bytes = serde_json::to_vec(&manifest).unwrap();
    let total = manifest_bytes.len() as u64 + files.values().map(|v|v.len() as u64).sum::<u64>();
    let id = hash(&manifest_bytes); let mut files = files; files.insert("asset-manifest.json".into(),manifest_bytes);
    let bytes = zip_files(files.into_iter().collect());
    (Bundle { id:id.clone(),version:version.into(),url:format!("/api/client/ui/{id}.zip"),size:bytes.len() as u64,sha256:hash(&bytes),unpacked_size:total,file_count:6,protocol_min:1,protocol_max:1,bridge_min:2,min_client_version:"1.0.10".into(),capabilities:CAPABILITIES.iter().map(|s|s.to_string()).collect() }, bytes)
}
fn zip_files(files: Vec<(String,Vec<u8>)>) -> Vec<u8> {
    let mut archive = ZipWriter::new(Cursor::new(Vec::new()));
    for (name,bytes) in files { archive.start_file(name,FileOptions::default().compression_method(zip::CompressionMethod::Deflated)).unwrap();archive.write_all(&bytes).unwrap(); }
    archive.finish().unwrap().into_inner()
}
fn unpack_files(bytes: &[u8]) -> Vec<(String,Vec<u8>)> {
    let mut zip = zip::ZipArchive::new(Cursor::new(bytes)).unwrap();
    (0..zip.len()).map(|index|{let mut file=zip.by_index(index).unwrap();let mut data=Vec::new();file.read_to_end(&mut data).unwrap();(file.name().to_string(),data)}).collect()
}
fn signed_release() -> Value { serde_json::from_str::<Value>(include_str!("fixtures/signed-envelopes.json")).unwrap() }
fn release(bundle: Bundle,sequence:u64) -> Release { Release { format:1,sequence,bundle,installers:BTreeMap::new() } }

#[test] fn rsa_signed_metadata_and_tampering() {
    let envelopes=signed_release(); let key=include_str!("fixtures/TEST-ONLY-public.pem");
    let bytes=serde_json::to_vec(&envelopes["valid"]).unwrap();
    let (release,_)=parse_release_with_key(&bytes,key).unwrap().unwrap(); assert_eq!(release.sequence,100);
    assert!(parse_release(&bytes).is_err(),"Synthetic signatures must never match the production trust root");
    for name in ["sequence","path","size","installer","format"] { assert!(parse_release_with_key(&serde_json::to_vec(&envelopes[name]).unwrap(),key).is_err(),"{name}"); }
    let mut tampered=envelopes["valid"].clone();let mut bytes=STANDARD.decode(tampered["payload"].as_str().unwrap()).unwrap();bytes[10]^=1;tampered["payload"]=json!(STANDARD.encode(bytes));
    assert!(parse_release_with_key(&serde_json::to_vec(&tampered).unwrap(),key).is_err());
    assert!(parse_release_with_key(&vec![b' ';METADATA_LIMIT as usize+1],key).is_err());
    assert!(parse_release_with_key(br#"{"ok":true,"available":false}"#,key).unwrap().is_none());
}
#[test] fn parses_beta_release_versions_and_preserves_semver_precedence() {
    assert_eq!(client_version("1.2.0"), Some(([1, 2, 0], false)));
    assert_eq!(client_version("1.2.0-beta.1"), Some(([1, 2, 0], true)));
    assert!(client_version("1.2.0-beta.0").is_none());
    assert!(client_version("1.2.0-beta.01").is_none());
    assert!(client_version("1.2.0-alpha.1").is_none());
    assert!(client_version("1.2").is_none());

    assert_eq!(version_meets_minimum([1, 2, 0], "1.2.0-beta.1"), Some(false));
    assert_eq!(version_meets_minimum([1, 2, 0], "1.2.0"), Some(true));
    assert_eq!(version_meets_minimum([1, 0, 10], "1.2.0-beta.1"), Some(true));
    assert_eq!(version_meets_minimum([2, 0, 0], "1.2.0-beta.1"), Some(false));
}

#[test] fn compatibility_checks_protocol_bridge_capability_and_version() {
    let (bundle,_)=fixture("A");assert!(compatibility(&bundle).is_ok());
    for change in 0..5 {let mut bad=bundle.clone();match change {0=>bad.protocol_min=2,1=>bad.bridge_min=3,2=>bad.capabilities.push("unknown".into()),3=>bad.min_client_version="99.0.0".into(),_=>bad.min_client_version="invalid".into()};assert!(compatibility(&bad).is_err());}
}
#[test] fn validates_zip_contents_and_hashes() {
    let (bundle,bytes)=fixture("A");let root=tempfile::tempdir().unwrap();extract(&bytes,&bundle,root.path()).unwrap();validate_root(root.path(),&bundle).unwrap();
    fs::write(root.path().join("shell.js"),b"corrupt").unwrap();assert!(validate_root(root.path(),&bundle).is_err());
    let mut damaged=bytes.clone();damaged[0]^=1;assert!(extract(&damaged,&bundle,tempfile::tempdir().unwrap().path()).is_err());
    for change in 0..4 {let mut bad=bundle.clone();match change {0=>bad.file_count+=1,1=>bad.unpacked_size-=1,2=>bad.id="0".repeat(64),_=>bad.size-=1};assert!(extract(&bytes,&bad,tempfile::tempdir().unwrap().path()).is_err());}
}
#[test] fn zip_rejects_unsafe_duplicate_missing_and_unlisted_entries() {
    let (bundle,bytes)=fixture("A");
    for path in ["../escape","/absolute","a\\b","C:drive","a/./b","NUL.txt","a. ","shell.js","SHELL.JS","unlisted.js"] {
        let mut files=unpack_files(&bytes);files.push((path.into(),b"x".to_vec()));let data=zip_files(files);let mut bad=bundle.clone();bad.file_count+=1;bad.unpacked_size+=1;bad.size=data.len() as u64;bad.sha256=hash(&data);
        assert!(extract(&data,&bad,tempfile::tempdir().unwrap().path()).is_err(),"{path}");
    }
    let mut files=unpack_files(&bytes);files.retain(|(name,_)|name!="bridge.js");let data=zip_files(files);let mut bad=bundle;bad.size=data.len() as u64;bad.sha256=hash(&data);bad.file_count-=1;
    assert!(extract(&data,&bad,tempfile::tempdir().unwrap().path()).is_err());
}
#[test] fn zip_rejects_symlink_and_invalid_file_hash() {
    let (bundle,bytes)=fixture("A");let mut archive=ZipWriter::new(Cursor::new(Vec::new()));
    for (name,data) in unpack_files(&bytes) { if name=="shell.js" {archive.add_symlink(name,"index.html",FileOptions::default()).unwrap();}else{archive.start_file(name,FileOptions::default()).unwrap();archive.write_all(&data).unwrap();} }
    let data=archive.finish().unwrap().into_inner();let mut bad=bundle.clone();bad.size=data.len() as u64;bad.sha256=hash(&data);assert!(extract(&data,&bad,tempfile::tempdir().unwrap().path()).is_err());
    let mut files=unpack_files(&bytes);files.iter_mut().find(|(name,_)|name=="shell.js").unwrap().1[0]^=1;let data=zip_files(files);bad=bundle;bad.size=data.len() as u64;bad.sha256=hash(&data);
    assert!(extract(&data,&bad,tempfile::tempdir().unwrap().path()).is_err());
}
#[test] fn pending_does_not_replace_active_and_next_launch_promotes_healthy_bundle() {
    let directory=tempfile::tempdir().unwrap();let server="https://synthetic.example";let mut store=Store::open(directory.path(),server).unwrap();
    let (a,bytes_a)=fixture("A");store.stage(&a,&bytes_a).unwrap();assert!(store.state.current.is_none());let active=store.launch(false).unwrap().unwrap();assert_eq!(active.id,a.id);store.ready(Some(&a.id)).unwrap();
    let (b,bytes_b)=fixture("B");store.stage(&b,&bytes_b).unwrap();assert_eq!(store.state.current.as_ref().unwrap().id,a.id);assert_eq!(fs::read(store.root(&a).join("shell.js")).unwrap(),b"synthetic A shell.js");
    let mut restarted=Store::open(directory.path(),server).unwrap();assert_eq!(restarted.launch(false).unwrap().unwrap().id,b.id);restarted.ready(Some(&b.id)).unwrap();
    assert_eq!(restarted.state.current.unwrap().id,b.id);assert_eq!(restarted.state.previous.unwrap().id,a.id);
}
#[test] fn unconfirmed_crash_rolls_back_and_quarantines_until_retry() {
    let directory=tempfile::tempdir().unwrap();let server="http://localhost";let mut store=Store::open(directory.path(),server).unwrap();
    let (a,bytes_a)=fixture("A");store.stage(&a,&bytes_a).unwrap();store.launch(false).unwrap();store.ready(Some(&a.id)).unwrap();
    let (b,bytes_b)=fixture("B");store.stage(&b,&bytes_b).unwrap();store.launch(false).unwrap();
    let mut restarted=Store::open(directory.path(),server).unwrap();assert_eq!(restarted.launch(false).unwrap().unwrap().id,a.id);assert!(restarted.state.quarantined.contains(&b.id));
    restarted.ready(Some(&a.id)).unwrap();restarted.retry(&b.id).unwrap();restarted.stage(&b,&bytes_b).unwrap();assert_eq!(restarted.launch(false).unwrap().unwrap().id,b.id);
}
#[test] fn failed_current_tries_previous_then_builtin_without_network() {
    let directory=tempfile::tempdir().unwrap();let mut store=Store::open(directory.path(),"http://localhost").unwrap();
    let (a,bytes_a)=fixture("A");store.stage(&a,&bytes_a).unwrap();store.launch(false).unwrap();store.ready(Some(&a.id)).unwrap();
    let (b,bytes_b)=fixture("B");store.stage(&b,&bytes_b).unwrap();store.launch(false).unwrap();store.ready(Some(&b.id)).unwrap();
    store.launch(false).unwrap();assert_eq!(store.failed().unwrap().unwrap().id,a.id);store.begin_trial(Some(a)).unwrap();assert!(store.failed().unwrap().is_none());
    assert!(store.launch(false).unwrap().is_none());assert_eq!(store.state.quarantined.len(),2);
    let mut cold=Store::open(directory.path(),"http://localhost").unwrap();assert!(cold.launch(false).unwrap().is_none());
}
#[test] fn signed_sequence_replay_and_newer_release_clear_stale_pending() {
    let directory=tempfile::tempdir().unwrap();let mut store=Store::open(directory.path(),"http://localhost").unwrap();let (a,bytes_a)=fixture("A");let (b,_)=fixture("B");
    store.accept(&release(a.clone(),10),"hash-A").unwrap();store.stage(&a,&bytes_a).unwrap();
    assert!(store.accept(&release(b.clone(),9),"hash-B").is_err());assert!(store.accept(&release(b.clone(),10),"hash-B").is_err());
    store.accept(&release(a.clone(),10),"hash-A").unwrap();assert!(store.state.pending.is_some());
    store.accept(&release(b,11),"hash-B").unwrap();assert!(store.state.pending.is_none());
    store.stage(&a,&bytes_a).unwrap();let mut incompatible=a;incompatible.bridge_min=99;store.accept(&release(incompatible,12),"hash-C").unwrap();assert!(store.state.pending.is_none());
    assert_eq!(Store::open(directory.path(),"http://localhost").unwrap().state.highest,12);
    assert_eq!(Store::open(directory.path(),"https://other.example").unwrap().state.highest,0);
}
#[test] fn failures_leave_working_state_and_atomic_file_intact() {
    let directory=tempfile::tempdir().unwrap();let mut store=Store::open(directory.path(),"http://localhost").unwrap();let (a,bytes_a)=fixture("A");
    store.stage(&a,&bytes_a).unwrap();store.launch(false).unwrap();store.ready(Some(&a.id)).unwrap();let before=fs::read(store.directory.join("state.json")).unwrap();
    let (b,bytes_b)=fixture("B");assert!(store.stage(&b,&bytes_b[..bytes_b.len()-1]).is_err());assert_eq!(fs::read(store.directory.join("state.json")).unwrap(),before);
    let blocked=store.directory.join("state.json");fs::remove_file(&blocked).unwrap();fs::create_dir(&blocked).unwrap();assert!(store.stage(&b,&bytes_b).is_err());assert_eq!(store.state.current.unwrap().id,a.id);assert!(store.state.pending.is_none());
}
#[test] fn cache_corruption_is_rejected_or_repaired_without_mutating_active() {
    let directory=tempfile::tempdir().unwrap();let mut store=Store::open(directory.path(),"http://localhost").unwrap();let (a,bytes)=fixture("A");
    store.stage(&a,&bytes).unwrap();fs::write(store.root(&a).join("shell.js"),b"corrupt").unwrap();store.stage(&a,&bytes).unwrap();assert!(store.available(&a));
    store.launch(false).unwrap();store.ready(Some(&a.id)).unwrap();fs::write(store.root(&a).join("shell.js"),b"corrupt").unwrap();assert!(store.stage(&a,&bytes).is_err());assert!(store.launch(false).unwrap().is_none());
}
#[test] fn corrupt_state_is_not_silently_reset() {
    let directory=tempfile::tempdir().unwrap();let store=Store::open(directory.path(),"http://localhost").unwrap();fs::write(store.directory.join("state.json"),b"broken").unwrap();assert!(Store::open(directory.path(),"http://localhost").is_err());
}
#[test] fn image_preview_accepts_only_supported_magic_and_limited_bytes() {
    let directory=tempfile::tempdir().unwrap();let path=directory.path().join("image");
    for (data,mime) in [(b"\x89PNG\r\n\x1a\n".as_slice(),"image/png"),(&[0xff,0xd8,0xff],"image/jpeg"),(b"RIFF0000WEBP","image/webp")] {fs::write(&path,data).unwrap();let value=image_preview(&path).unwrap();assert_eq!(value["mime"],mime);assert_eq!(STANDARD.decode(value["body"].as_str().unwrap()).unwrap(),data);}
    fs::write(&path,b"<svg></svg>").unwrap();assert!(image_preview(&path).is_err());let file=fs::File::create(&path).unwrap();file.set_len(10*1024*1024+1).unwrap();assert!(image_preview(&path).is_err());
}
#[test] fn python_publisher_interoperability_fixture() {
    // Opt-in fixture is generated by the real Python publisher with a synthetic key.
    let Some(directory)=std::env::var_os("XTYF_UI_TEST_FIXTURE_DIR").map(PathBuf::from) else{return;};
    let key=fs::read_to_string(directory.join("public.pem")).unwrap();let metadata=fs::read(directory.join("update.json")).unwrap();
    let (release,_)=parse_release_with_key(&metadata,&key).unwrap().unwrap();compatibility(&release.bundle).unwrap();
    let bytes=fs::read(directory.join("bundles").join(format!("{}.zip",release.bundle.id))).unwrap();let root=tempfile::tempdir().unwrap();extract(&bytes,&release.bundle,root.path()).unwrap();validate_root(root.path(),&release.bundle).unwrap();
}
