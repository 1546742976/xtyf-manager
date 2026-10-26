# Web First / PWA 第一阶段

推荐入口为 HTTPS 网站及其可选 PWA 安装。原 Flask/Jinja 页面、账户主题、Cookie、CSRF、权限、业务 API、扫码与文件流程继续共用；没有第二套前端、离线令牌、原生扫码或业务同步队列。旧客户端及签名 UI 更新、兼容 API、发布 CI 均保留。`clients/config.json` 的旧 HTTP 默认地址未变，本次不发布客户端安装包、不部署生产、不合并 PR。

## 路由、资源与缓存边界

`modules/pwa.py` 提供公开的 `/manifest.webmanifest`（`application/manifest+json`）与根作用域 `/sw.js`（`text/javascript`、`Service-Worker-Allowed: /`）。二者 `no-cache` 且不创建会话/CSRF Cookie，在登录页和首次改密前也能访问。其他业务响应维持 `no-store`。公共头部接入 base、资金、发票、错误页；客户端本地模板编译器只排除网页 PWA 入口，不改变其桥接或更新生命周期。

独立 PNG 图标来自 `clients/assets/xtyf-logo.svg` 的原始路径与颜色，保持比例；普通 192/512 与 Apple 180 图标加 6% 边距和浅色背景。maskable 512 将标志置于中央 280×296 区域，非背景像素完整落入直径 80% 的安全圆内，由像素测试验证。没有外部 CDN 图标。

精确许可的缓存资源只有：

- `/static/pwa/offline.html`：自包含、无脚本、无用户数据的离线说明。
- `/static/pwa/icon-192.png`、`icon-512.png`、`maskable-512.png`、`apple-touch-icon.png`。
- `/static/pwa.js`、`/static/pwa.css`：安装引导及断网提示。

这些路径还必须携带**当前内容版本的唯一 `?v=…` 查询串**。普通静态路径、额外查询参数、其他版本、跨域、Authorization/Range 请求不进入缓存。业务 CSS/JS、Bootstrap、ZXing、PDF.js、头像、附件、下载、API、认证和所有写请求均走网络；大型解码/PDF 资源不预缓存。这里刻意保留较窄的缓存集合，避免旧 Worker 为新 HTML 提供旧业务脚本。

缓存采用网络优先，仅网络异常时对许可的精确版本读取缓存；HTTP 4xx/5xx 原样返回。写入前检查 200、无重定向、许可 MIME（脚本兼容 text/javascript 与 application/javascript）、无 `no-store/private`、无 Cookie/认证 Vary、无 CSRF 响应头，并逐字节校验受限长度和 SHA-256。公共读取使用 `credentials: omit`；浏览器可能过滤 Set-Cookie，因此安全性同时依赖精确路径、无凭据请求与内容校验，不能只依赖该头的可见性。gzip/br 传输长度与解码后长度分别处理。缓存配额错误不会破坏成功的网络响应。

上限为 7 个固定键，每个 ≤512 KiB，全部 ≤2 MiB，实际资源约 80 KiB；上线构建超限会失败。缓存名 `xtyf-pwa-public-<内容哈希>` 同时覆盖许可资源和 SW 策略源码的变化，不依赖生产目录保留 Git。安装阶段完整校验并预缓存这组小型公共资源，失败删除本版本半成品；运行时只读缓存、不创建或写入命名缓存，避免退役期间在途请求重新创建已删除缓存；激活只清理本前缀旧缓存。正常升级没有 `skipWaiting`、强制刷新或 Cookie/本地存储清空。

仅列出的业务页面导航可在断网时返回离线说明；API、附件、PDF 等即便以顶层导航打开也只走网络，不会用 HTML 冒充下载结果。动态 HTML 从不写入 Cache Storage。站点原本的 `no-store` 继续约束浏览器 HTTP 缓存。

## 配置与部署检查

| 变量 | 默认 | 用途 |
| --- | --- | --- |
| `PROGRESS_COOKIE_SECURE` | `0` | 生产 HTTPS 入口验收后设 `1`，使会话 Cookie 带 Secure；本地 HTTP 保持 `0` |
| `PROGRESS_PWA_ENABLED` | `1` | `0` 隐藏安装入口，并让 `/sw.js` 返回安全退役脚本 |

不根据任意客户端 `X-Forwarded-Proto` 自动开启安全开关，不引入宽泛 ProxyFix。现有 `TRUSTED_PROXY_CIDRS` 用于来源 IP 判断，不等于信任协议/Host；仅配置实际代理地址。Manifest/资源链接使用同源相对路径，不依赖 Flask 推断外部 HTTPS 协议。

沿用公网入口 Nginx → FRP → Debian 树莓派 Gunicorn 的现有链路。后端监听 HTTP 不影响浏览器看到的 HTTPS，但入口应重定向 HTTP、正确传递现有 Host/代理头且无 mixed content。Nginx 不应把 `/sw.js` 或 `/manifest.webmanifest` 重写成首页、登录页或静态 404，也不要长期缓存它们；有独立 static location 时必须同步同一提交的 `static/pwa*`。

以下为**上线后由维护者执行**的只读检查，本次没有改生产配置：

```bash
curl -I http://manager.skywalker-rm.online/
curl -I https://manager.skywalker-rm.online/manifest.webmanifest
curl -I https://manager.skywalker-rm.online/sw.js
curl -fsS https://manager.skywalker-rm.online/manifest.webmanifest | python3 -m json.tool
openssl s_client -connect manager.skywalker-rm.online:443 -servername manager.skywalker-rm.online -verify_return_error </dev/null
```

还需在浏览器 Network/Application 中检查证书、作用域、Content-Type、资源 200、无混合内容及实际 Cookie 的 Secure/HttpOnly/SameSite。部署阶段在 `rm-progress` 的现有环境配置中添加 `PROGRESS_COOKIE_SECURE=1`，重启后重新登录验收；本地回归不强制 Secure Cookie。

2026-10-26 执行环境只观察到 HTTP 301 跳到 HTTPS、HTTPS `/` 302 跳到登录，后续登录请求返回 400。不能据此确认完整证书链、代理头、混合内容或实际登录；上述仍为线上待验收。

`deploy/package_release.py` 继续从 HEAD 打包，并显式要求 PWA 模块、模板、SW 源码、离线页和四张 PNG 全部存在。隔离 Git 归档测试校验每个文件字节，不调用正式打包或签名。提交并按原网站发布流程部署后才会生效；不需要改客户端默认地址或生成 EXE/APK/DEB/RPM。

## 升级与回滚

常规升级：按现有发布/备份流程部署同一个 HEAD，打开账户页；有旧窗口时新版 Worker 保持 waiting。保存草稿并关闭所有该浏览器配置文件中的本站窗口后重开，确认新缓存版本且旧前缀缓存已删除。不同浏览器配置文件分别验收。

出现缓存问题时，先保留当前 `/sw.js` 处理能力，再在服务环境配置中设置 `PROGRESS_PWA_ENABLED=0` 并重启业务服务。该脚本立即接管、不强制刷新，**仅删除 `xtyf-pwa-public-` 缓存并注销自己的注册**，保留 Cookie、其他 Cache Storage 和 localStorage。已打开页面的脚本不会被撤回；保存工作后重新打开。可通过开发者控制台主动检查退役版本：

```javascript
navigator.serviceWorker.getRegistration('/').then(registration => registration?.update())
```

确认注册消失、可在线登录且无自有缓存。需要回滚旧业务代码时，继续保留退役 `/sw.js` 路由直到既有设备都获取过退役脚本；只把 SW 文件删成 404 不能视为完成撤回。修复后恢复变量为 `1` 并重启，正常重新注册。不在每次网页加载时清 Cookie，不以强制注销作为升级步骤。

## 可复现的自动化

```bash
python -m unittest discover -s tests -p test_pwa.py -v
node --test tests/js/pwa_worker.test.cjs
node tests/browser/pwa.cjs
```

浏览器用例使用 Playwright Chromium 和 `tests/browser/pwa_server.py` 的 loopback 隔离数据库、会话密钥与静态资源副本；没有真实业务数据。测试中的发布/退役路由仅存在于该测试服务器。CI 新增 `pwa` 浏览器分组，Python 新模块自动归入现有 platform shard，JS 纳入现有全量测试；旧客户端 CI 保持启用。

既有资产、头像及账户布局用例需要用 Playwright 路由模拟丢失响应、失败上传等故障，相关浏览器上下文显式设置 `serviceWorkers: 'block'`，避免 Service Worker 绕过这些拦截器。`pwa.cjs` 保持真实 Worker 注册、联网 API 与附件/PDF 流程，不使用这些故障路由；账户布局回归同时检查新增安装卡片在桌面跨两列、移动端按顺序排列且无溢出。

覆盖：公开 MIME/无会话响应、所有主要模板、PNG 实际尺寸及 maskable 安全圆、业务 no-store/CSRF、有限许可表/内容版本变化、HTTP 源码归档、错误响应及隐私头拒绝缓存、断网 API 失败及导航说明、永久 Cookie 的浏览器配置文件关闭重开、独立上下文账户主题刷新、相机授权失败的相册/手动兜底、真实 PDF 预览/下载、图片 FormData 上传/受保护附件下载及私有 PDF 错误、跨部署 waiting/草稿保留/旧缓存清理、退出/换账号、只清理自有缓存的退役。实际安装提示受浏览器策略影响，自动化不冒充安装成功。

## 本地执行结果

2026-10-26：全量 Python 回归 719 项通过，JS 单测 167 项通过；新增 PWA 后端 7 项、Worker 单测 8 项、旧客户端模板兼容 7 项通过。PWA 浏览器完整流程在隔离的 Chromium 153 无头环境通过；CI 使用现有 Playwright 1.57 镜像及独立 `pwa` 分组再次运行。浏览器结果不表示已在真实设备安装，未生成正式客户端安装包、未运行正式签名发布或生产部署。

## 尚需人工验收

| 场景 | 状态与要求 |
| --- | --- |
| Android Chrome / Edge 真机 | 未验收：安装、独立窗口、深链接/返回、关闭重开会话、主题 |
| Windows Chrome / Edge 真机 | 未验收：安装/卸载、独立窗口导航、真实下载与打印 |
| iOS Safari | 未验收：添加到主屏幕、独立存储/登录、相机和文件操作差异 |
| 摄像头 | 未验收：真实 Android 连续 ≥5 分钟、重复识别、切换应用/隐藏/关闭后释放摄像头、相册兜底；现有 Web 扫码采样与 Worker 优化保持原样 |
| 文件 | 未验收：真实图片/附件上传下载、标签预览与实际尺寸打印、各平台保存对话框 |
| 生产站点 | 未验收：完整受信证书链、代理头、混合内容、部署后 Manifest/SW MIME 与作用域、Secure Cookie |
| 升级与撤回 | 自动化已提供隔离用例；真实设备跨版本安装、回退、不同浏览器/账号仍待验收 |

是否停用旧客户端属于后续独立决策，应以以上真机和生产验收结果为依据。
