# 安装包内置界面

桌面和 Android 使用本目录构建的同一份静态资源。业务页面来自项目现有 `templates/` 与 `static/`，启动和导航不会请求服务器 HTML。客户端可加载安装包内置资源或已经由原生层验签、完整校验的页面更新；同一窗口始终使用同一个资源版本。

在项目根目录运行 `python clients/build_frontend.py`。构建器使用 Jinja AST 生成普通 JavaScript 渲染函数，不在客户端解释模板或调用 `eval`。不支持的模板语法会使构建失败。构建只读取公共模板、静态资源和客户端版本配置，不导入 Flask，不读取业务数据库。`asset-manifest.json` 记录版本、协议和每个资源的 SHA-256。

## 页面和通信

- `shell.js` 管理本地路由、初始化、服务器设置、会话变化及附件预览。每次导航创建新的本地页面文档，销毁上一页的定时器、请求和对象 URL。
- `page-runtime.js` 为现有业务脚本适配 `fetch`、`FormData`、上传、原生文件句柄和本地导航。原始网站文件保持不变。发票后台任务通过原有查询接口轮询。
- `bridge.js` 选择 Tauri `native_call` 或 Android 根页面专属 WebMessagePort。业务请求只传相对路径，Cookie、CSRF 和服务器重定向由原生端管理；网页不直接连接服务器。
- `update-runtime.js` 在启动、前台每 30 分钟、离开超过 5 分钟返回前台时检查更新。后台不持续轮询；Android 同时提供 Activity 前后台事件。下载完成不重载当前页面，可在设置中主动重载并确认未保存内容的风险。版本验签、暂存和失败回退均由原生层负责，协议见 `clients/update-protocol.md`。
- 页面和原生桥初始化完成后发送 `ui_ready`，不等待服务器联网成功。不兼容的 bootstrap 阻止业务请求，仍可进入服务器设置及更新。安装包保存仅使用原生返回的已验证清单链接和 SHA-256，不自动安装。
- 头像编辑按 `image_preview` 能力启用。原生选择句柄读取原图字节用于裁剪预览；上传继续携带原句柄，由服务器裁剪。头像展示通过认证资源通道读取，上传或恢复默认后同步 bootstrap，保留其他资料草稿。
- `external.html` 仅是桌面独立浏览窗口的本地返回/关闭栏。远程浏览视图不加载这个页面的脚本，也没有原生命令权限。
- 图片及 PDF 由带认证的原生传输读取。PDF.js 与 worker、CMap、字体和图像编解码资源均随包内置，关闭 JavaScript 求值和 XFA。

## 固定依赖

`vendor-lock.json` 记录 Bootstrap 5.3.3、Bootstrap Icons 1.11.3、PDF.js 5.4.624 的 npm 官方来源、SHA-512 和各文件 SHA-256。普通构建不联网，先验证本地文件的锁定哈希。维护者更新版本时才运行 `python clients/frontend/vendor_dependencies.py`，并审阅锁文件和上游许可。`vendor/` 中保留相应许可文件。

## 验证

```powershell
python clients/frontend/test_frontend.py
# 使用安装有项目依赖及 Python Playwright/Chromium 的解释器
python clients/frontend/test_browser.py
# 使用 CI 的 Playwright 1.56.1 Node 环境，验证更新及原生句柄头像适配
node --test tests/js/client_updates.test.cjs clients/frontend/test_bridge.cjs
node tests/browser/client_updates_avatar.cjs
```

模板测试比较每个模板在角色、主题、首次改密和匿名场景中的服务端 Jinja 输出。浏览器集成测试使用临时数据库及模拟原生通信，检查真实 API 登录、全部业务路由、文件句柄、PDF、手机布局、返回、主题、退出、离线和旧服务器提示；截图及报告写到 `.build-tools/client-ui-tests/`。新增更新与头像场景输出到 `.build-tools/client-updates-avatar-check/`，覆盖 Chromium 和 WebKit 的真实本地页面及隔离 API；其中 A/B 更新状态由模拟原生桥提供。这些检查不代替 WebView2、Android 真机或 Linux WebKitGTK 的运行验收。
