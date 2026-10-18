package online.skywalker.manager;

import org.json.*;
import com.sun.net.httpserver.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import java.util.zip.*;

/** Executes the production store and native transport against synthetic signed loopback releases. */
public final class UiUpdateStoreTest {
    private static int assertions;
    private static KeyPair keys;
    private static String pem;
    private static final AtomicReference<String> envelope = new AtomicReference<>();
    private static final AtomicReference<byte[]> archive = new AtomicReference<>();
    private static final AtomicInteger archiveHits = new AtomicInteger(), metadataHits = new AtomicInteger();
    private static void check(boolean valid, String message) { assertions++; if (!valid) throw new AssertionError(message); }
    interface Action { void run() throws Exception; }
    private static void fails(Action action, String message) throws Exception {
        try { action.run(); throw new AssertionError(message); } catch (IOException expected) { assertions++; }
    }
    static final class Bundle {
        byte[] bytes; String id, version; long total; int count;
        Bundle(String version, Map<String, byte[]> additions) throws Exception {
            this.version = version; Map<String, byte[]> entries = new LinkedHashMap<>();
            for (String name : Arrays.asList("index.html", "shell.js", "bridge.js", "page-runtime.js", "templates.js")) entries.put(name, (version + ":" + name).getBytes(StandardCharsets.UTF_8));
            entries.putAll(additions); JSONObject hashes = new JSONObject(), sizes = new JSONObject();
            for (Map.Entry<String, byte[]> entry : entries.entrySet()) { hashes.put(entry.getKey(), UiUpdateStore.hash(entry.getValue())); sizes.put(entry.getKey(), entry.getValue().length); }
            byte[] manifest = new JSONObject().put("protocolVersion",1).put("version","1.0.10").put("templates",new JSONArray()).put("files",hashes).put("sizes",sizes).toString().getBytes(StandardCharsets.UTF_8);
            id = UiUpdateStore.hash(manifest); entries.put("asset-manifest.json",manifest);
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            try (ZipOutputStream zip = new ZipOutputStream(output)) {
                for (Map.Entry<String, byte[]> entry : entries.entrySet()) { zip.putNextEntry(new ZipEntry(entry.getKey())); zip.write(entry.getValue()); zip.closeEntry(); total += entry.getValue().length; }
            }
            bytes = output.toByteArray(); count = entries.size();
        }
        JSONObject release(long sequence) throws Exception {
            return new JSONObject().put("format",1).put("sequence",sequence).put("installers",new JSONObject()).put("bundle",new JSONObject()
                .put("id",id).put("version",version).put("url","/api/client/ui/"+id+".zip").put("size",bytes.length).put("sha256",UiUpdateStore.hash(bytes))
                .put("unpackedSize",total).put("fileCount",count).put("protocolMin",1).put("protocolMax",1).put("bridgeMin",2)
                .put("minClientVersion","1.0.10").put("capabilities",new JSONArray(Arrays.asList("ui_updates","image_preview"))));
        }
    }
    private static String signed(JSONObject release) throws Exception {
        byte[] payload = release.toString().getBytes(StandardCharsets.UTF_8); Signature signature = Signature.getInstance("SHA256withRSA"); signature.initSign(keys.getPrivate()); signature.update(payload);
        return new JSONObject().put("ok",true).put("available",true).put("payload",Base64.getEncoder().encodeToString(payload)).put("signature",Base64.getEncoder().encodeToString(signature.sign())).toString();
    }
    private static void publish(Bundle bundle, long sequence) throws Exception { archive.set(bundle.bytes); envelope.set(signed(bundle.release(sequence))); }
    private static void send(HttpExchange exchange, byte[] bytes) throws IOException { exchange.sendResponseHeaders(200,bytes.length); try(OutputStream output = exchange.getResponseBody()) { output.write(bytes); } }
    private static UiUpdateStore store(File data, String server) throws Exception { return new UiUpdateStore(data,server,"1.0.10",pem,new NativeHttpClient(server,"",ignored -> {})); }
    private static File stateFile(File data, String server) throws Exception { return new File(new File(data,UiUpdateStore.hash(server.getBytes(StandardCharsets.UTF_8))),"state.json"); }
    public static void main(String[] args) throws Exception {
        KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA"); generator.initialize(3072); keys = generator.generateKeyPair();
        pem = "-----BEGIN PUBLIC KEY-----\n"+Base64.getEncoder().encodeToString(keys.getPublic().getEncoded())+"\n-----END PUBLIC KEY-----";
        File data = Files.createTempDirectory("xtyf-ui-store-").toFile();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1",0),0); ExecutorService executor = Executors.newCachedThreadPool(); server.setExecutor(executor);
        CountDownLatch blocked = new CountDownLatch(1), release = new CountDownLatch(1); AtomicBoolean block = new AtomicBoolean();
        server.createContext("/api/client/updates", exchange -> { metadataHits.incrementAndGet(); if(block.get()) { blocked.countDown(); try { release.await(10,TimeUnit.SECONDS); } catch(InterruptedException error) { Thread.currentThread().interrupt(); } } send(exchange,envelope.get().getBytes(StandardCharsets.UTF_8)); });
        server.createContext("/api/client/ui/", exchange -> { archiveHits.incrementAndGet(); send(exchange,archive.get()); }); server.start();
        String origin = "http://127.0.0.1:"+server.getAddress().getPort();
        try {
            Bundle a = new Bundle("A",Collections.emptyMap()), b = new Bundle("B",Collections.emptyMap()), c = new Bundle("C",Collections.emptyMap());
            UiUpdateStore store = store(data,origin); check(store.startView(true)==null,"Initially bundled"); store.ready();
            envelope.set("{\"ok\":true,\"available\":false}"); check("unavailable".equals(store.check(false).getString("status")),"Absent release");
            publish(a,1); check("pending".equals(store.check(false).getString("status")),"A is pending");
            check("1.0.10".equals(store.status().getString("currentVersion")),"Download must not replace active view");
            int hits=archiveHits.get(); store.check(false); check(archiveHits.get()==hits,"Do not download pending release twice");
            File aRoot=store.startView(true); check(aRoot.getName().equals(a.id),"A activated at cold start"); store.ready();
            publish(b,2); check("pending".equals(store.check(false).getString("status")),"B downloads");
            check("A".equals(store.status().getString("currentVersion")) && new File(aRoot,"shell.js").isFile(),"Running view remains pinned to A");
            UiUpdateStore second=store(data,origin); check(second.startView(true).getName().equals(b.id),"Next process activates B");
            check(second.failView().getName().equals(a.id),"Watchdog restores A"); second.ready();
            hits=archiveHits.get(); check("failed".equals(second.check(false).getString("status")) && archiveHits.get()==hits,"Quarantine avoids automatic reactivation");
            check("pending".equals(second.check(true).getString("status")),"Manual retry downloads quarantined release");
            check(second.startView(true).getName().equals(b.id),"Retry starts B"); second.ready();
            publish(c,3); second.check(false); second.startView(true); // Simulate crash without ui_ready.
            UiUpdateStore crashed=store(data,origin); check(crashed.startView(true).getName().equals(b.id),"Crash restores B");
            check(crashed.failView()==null,"Failed fallback uses bundled UI"); crashed.ready();
            publish(a,4); crashed.check(false); crashed.startView(true); crashed.ready();
            publish(b,3); check("failed".equals(crashed.check(false).getString("status")),"Reject old signed sequence");
            publish(b,4); check("failed".equals(crashed.check(false).getString("status")),"Reject changed same sequence");
            JSONObject incompatible=b.release(5); incompatible.getJSONObject("bundle").put("bridgeMin",3);
            incompatible.getJSONObject("installers").put("android",new JSONObject().put("version","1.1.0").put("url","/downloads/client.apk").put("sha256",UiUpdateStore.hash(new byte[]{1})));
            envelope.set(signed(incompatible)); check("incompatible".equals(crashed.check(false).getString("status")),"Reject insufficient bridge");
            check("/downloads/client.apk".equals(crashed.status().getJSONObject("installer").getString("url")),"Expose signed matching installer");
            crashed.validateInstaller("/downloads/client.apk",UiUpdateStore.hash(new byte[]{1})); check(true,"Only signed installer download accepted");
            fails(() -> crashed.validateInstaller("/downloads/other.apk",UiUpdateStore.hash(new byte[]{1})),"Reject other installer path");
            fails(() -> crashed.validateInstaller("/downloads/client.apk",UiUpdateStore.hash(new byte[]{2})),"Reject changed installer digest");
            JSONObject wrongCapability=b.release(6); wrongCapability.getJSONObject("bundle").put("capabilities",new JSONArray(Arrays.asList("unknown"))); envelope.set(signed(wrongCapability));
            check("incompatible".equals(crashed.check(false).getString("status")),"Capabilities gate");
            JSONObject invalid=b.release(7); invalid.getJSONObject("installers").put("android",new JSONObject().put("version","1.1.0").put("url","//evil.test/pkg").put("sha256",UiUpdateStore.hash(new byte[]{1}))); envelope.set(signed(invalid));
            check("failed".equals(crashed.check(false).getString("status")),"Reject installer outside server");
            publish(b,7); JSONObject corrupted=new JSONObject(envelope.get()); corrupted.put("payload",Base64.getEncoder().encodeToString("{}".getBytes(StandardCharsets.UTF_8))); envelope.set(corrupted.toString());
            check("failed".equals(crashed.check(false).getString("status")),"Bad signature refused");
            publish(b,7); archive.set(new byte[]{1,2,3}); check("failed".equals(crashed.check(false).getString("status")),"Interrupted/truncated package refused");
            publish(b,7); byte[] damaged=b.bytes.clone(); damaged[40]^=1; archive.set(damaged); check("failed".equals(crashed.check(false).getString("status")),"Hash failure refused");
            publish(b,8); File state=stateFile(data,origin), saved=new File(state.getParentFile(),"saved.json"); Files.move(state.toPath(),saved.toPath()); check(state.mkdir(),"Create disk failure fixture");
            check("failed".equals(crashed.check(false).getString("status")),"Atomic state-write failure reported");
            Files.delete(state.toPath()); Files.move(saved.toPath(),state.toPath()); check("A".equals(crashed.status().getString("currentVersion")),"Disk failure preserves active version");
            publish(b,9); block.set(true); UiUpdateStore parallel=store(data,origin); parallel.startView(false); parallel.ready(); int reads=metadataHits.get();
            Future<JSONObject> pending=executor.submit(() -> parallel.check(false)); check(blocked.await(5,TimeUnit.SECONDS),"Concurrent request reaches server");
            parallel.check(false); check(metadataHits.get()==reads+1,"Concurrent checks coalesce"); parallel.retire(); release.countDown();
            check("failed".equals(pending.get(5,TimeUnit.SECONDS).getString("status")),"Retired server cannot publish pending"); block.set(false);
            UiUpdateStore separate=store(data,origin.replace("127.0.0.1","localhost")); check(separate.startView(true)==null,"Server roots isolated");
            UiUpdateStore recovery=store(new File(data,"recovery"),origin); recovery.startView(true); recovery.ready(); publish(a,20); recovery.check(false); File recoveryA=recovery.startView(true); recovery.ready();
            publish(b,21); recovery.check(false); publish(a,22); recovery.check(false); check(recovery.status().isNull("pendingVersion"),"New signed release supersedes obsolete pending bundle");
            publish(b,23); recovery.check(false); File recoveryB=recovery.startView(true); recovery.ready(); Files.write(new File(recoveryB,"shell.js").toPath(),new byte[]{0});
            UiUpdateStore corrupt=store(new File(data,"recovery"),origin); check(corrupt.startView(true).equals(recoveryA),"Corrupt current assets fall back to previous verified root"); corrupt.ready();
            publish(b,24); check("pending".equals(corrupt.check(true).getString("status")),"Retry rebuilds inactive corrupt directory");
            check(corrupt.startView(true).getName().equals(b.id),"Rebuilt bundle starts"); corrupt.ready();
            hits=metadataHits.get(); UiUpdateStore offline=store(new File(data,"recovery"),origin); offline.startView(true); offline.ready(); check(metadataHits.get()==hits,"Cold start and readiness require no network");
            File consecutiveData=new File(data,"consecutive-failures"); UiUpdateStore consecutive=store(consecutiveData,origin); consecutive.startView(true); consecutive.ready();
            publish(a,30); consecutive.check(false); consecutive.startView(true); consecutive.ready(); publish(b,31); consecutive.check(false); consecutive.startView(true);
            check(consecutive.failView().getName().equals(a.id),"First failed trial falls back to A"); check(consecutive.failView()==null,"Second failed trial falls back to bundled UI"); consecutive.ready();
            JSONObject failureState=new JSONObject(new String(Files.readAllBytes(stateFile(consecutiveData,origin).toPath()),StandardCharsets.UTF_8));
            check(failureState.getJSONArray("quarantined").length()==2,"Both consecutive failed resource IDs remain isolated");
            UiUpdateStore reopenedFailures=store(consecutiveData,origin); check(reopenedFailures.startView(true)==null,"Cold start cannot revive either failed resource"); reopenedFailures.ready();
            hits=archiveHits.get(); publish(b,32); check("failed".equals(reopenedFailures.check(false).getString("status")),"New manifest sequence cannot silently retry failed B");
            publish(a,33); check("failed".equals(reopenedFailures.check(false).getString("status")),"New manifest sequence cannot silently retry failed A"); check(archiveHits.get()==hits,"No failed resource downloaded automatically");
            publish(b,34); check("pending".equals(reopenedFailures.check(true).getString("status")),"Explicit retry unlocks selected failed resource only");
            hits=archiveHits.get(); publish(a,35); check("failed".equals(reopenedFailures.check(false).getString("status")) && archiveHits.get()==hits,"Retrying B leaves A isolated");
            publish(c,36); check("pending".equals(reopenedFailures.check(false).getString("status")),"New resource C is allowed after consecutive failures");
            File corruptData=new File(data,"damaged-state"); store(corruptData,origin); File corruptFile=stateFile(corruptData,origin);
            for(String damage:Arrays.asList("broken JSON", "{\"highest\":\"invalid\"}", "{\"current\":\"../outside\"}", "{\"quarantined\":[3]}")) {
                byte[] original=damage.getBytes(StandardCharsets.UTF_8); Files.write(corruptFile.toPath(),original); int beforeRequests=metadataHits.get();
                UiUpdateStore damagedStore=store(corruptData,origin); check(damagedStore.startView(true)==null,"Damaged update state still allows bundled UI startup"); damagedStore.ready();
                check("failed".equals(damagedStore.check(true).getString("status")),"Damaged ledger disables updates without resetting anti-replay protection");
                check(metadataHits.get()==beforeRequests && Arrays.equals(Files.readAllBytes(corruptFile.toPath()),original),"Damaged original state preserved verbatim without network");
            }
            for(String path:Arrays.asList("../a","a/../b","/a","a\\b","a//b","a/./b","C:a")) fails(() -> UiUpdateStore.assetPath(path),"Path rejection "+path);
            for(String path:Arrays.asList("//evil.test/a","https://evil.test/a","/a#b","/a\\b","/%2e%2e/a")) fails(() -> UiUpdateStore.serverPath(path),"URL rejection "+path);
            File goodZip=new File(data,"good.zip"); Files.write(goodZip.toPath(),b.bytes);
            UiUpdateStore.unpack(goodZip,new File(data,"good"),b.id,b.total,b.count); check(true,"Real signed package validates");
            fails(() -> UiUpdateStore.unpack(goodZip,new File(data,"bad-size"),b.id,b.total-1,b.count),"Declared unpack limit");
            fails(() -> UiUpdateStore.unpack(goodZip,new File(data,"bad-count"),b.id,b.total,b.count-1),"Declared count");
            fails(() -> UiUpdateStore.unpack(goodZip,new File(data,"bad-id"),a.id,b.total,b.count),"Manifest hash");
            Bundle traversal=new Bundle("bad",Collections.singletonMap("../escape",new byte[]{1})); File badZip=new File(data,"bad.zip"); Files.write(badZip.toPath(),traversal.bytes);
            fails(() -> UiUpdateStore.unpack(badZip,new File(data,"traversal"),traversal.id,traversal.total,traversal.count),"ZIP traversal");
            byte[] symlink=b.bytes.clone(); for(int i=0;i<symlink.length-46;i++) if((symlink[i]&255)==0x50 && (symlink[i+1]&255)==0x4b && symlink[i+2]==1 && symlink[i+3]==2) { symlink[i+40]=(byte)0xff;symlink[i+41]=(byte)0xa1;break; }
            Files.write(badZip.toPath(),symlink); fails(() -> UiUpdateStore.unpack(badZip,new File(data,"symlink"),b.id,b.total,b.count),"ZIP symlink");
            check("image/jpeg".equals(UiUpdateStore.imageMime(new byte[]{(byte)255,(byte)216,(byte)255})),"JPEG preview");
            check("image/png".equals(UiUpdateStore.imageMime(new byte[]{(byte)137,80,78,71,13,10,26,10})),"PNG preview");
            check("image/webp".equals(UiUpdateStore.imageMime("RIFF1234WEBP".getBytes(StandardCharsets.US_ASCII))),"WebP preview");
            fails(() -> UiUpdateStore.imageMime("<svg/>".getBytes(StandardCharsets.UTF_8)),"Reject SVG preview");
            fails(() -> UiUpdateStore.read(new ByteArrayInputStream(new byte[10*1024*1024+1]),10*1024*1024),"Preview size bound");
            if(args.length>0) {
                File fixture=new File(args[0]); String realEnvelope=new String(Files.readAllBytes(new File(fixture,"update.json").toPath()),StandardCharsets.UTF_8);
                JSONObject realRelease=new JSONObject(new String(Base64.getDecoder().decode(new JSONObject(realEnvelope).getString("payload")),StandardCharsets.UTF_8));
                JSONObject realBundle=realRelease.getJSONObject("bundle"); envelope.set(realEnvelope); archive.set(Files.readAllBytes(new File(fixture,"bundles/"+realBundle.getString("id")+".zip").toPath()));
                String realKey=new String(Files.readAllBytes(new File(fixture,"public.pem").toPath()),StandardCharsets.US_ASCII);
                UiUpdateStore realStore=new UiUpdateStore(new File(data,"python-fixture"),origin,"1.0.10",realKey,new NativeHttpClient(origin,"",ignored -> {})); realStore.startView(true); realStore.ready();
                JSONObject checked=realStore.check(false); check("pending".equals(checked.getString("status")),"Python publisher accepted: "+checked);
                File realRoot=realStore.startView(true); check(realRoot.getName().equals(realBundle.getString("id")),"Real publisher bundle active"); realStore.ready();
                check(realBundle.getString("version").equals(realStore.status().getString("currentVersion")),"Independent source version retained");
                try(java.util.stream.Stream<Path> paths=Files.walk(realRoot.toPath())) { check(paths.filter(Files::isRegularFile).count()==realBundle.getInt("fileCount"),"All real publisher files extracted"); }
                System.out.println("Cross-language Python publisher fixture: "+realBundle.getInt("fileCount")+" files, "+realBundle.getString("version"));
            }
            System.out.println("UiUpdateStoreTest: "+assertions+" assertions passed (real RSA, HTTP, ZIP, atomic state and A/B lifecycle)");
        } finally { release.countDown(); server.stop(0); executor.shutdownNow(); try(java.util.stream.Stream<Path> paths=Files.walk(data.toPath())) { for(Path path:(Iterable<Path>)paths.sorted(Comparator.reverseOrder())::iterator) Files.deleteIfExists(path); } }
    }
}
