# PR #59 评论处理

识别查询改为最多四个活动请求，排队与查询共用 15 秒期限。每个清单条目只有一个有效查询；重试、移除、关闭或重开会中止旧请求并释放调度位置。支持 AbortSignal 的网页会实际取消网络请求，旧原生桥即便忽略信号也不会阻塞后续查询或写入迟到结果。查询只读，清单去重和库存提交确认保持原样。

- `node --test tests/js/asset_camera_retry.test.cjs tests/js/asset_codes.test.cjs`：12 项通过，包含永不 settle、同码重试、停止后迟到响应和 20 个挂起标签的四并发上限。
- `node tests/browser/assets_serialized.cjs`：Chromium / WebKit 均通过，含扫码、结果不明重试及 PDF 预览。
- `python tests/verify_release_pdf_assets.py`：真实执行 `deploy/package_release.py`，以 HEAD 源码的隔离副本和一次性公私钥打包。除测试公钥外不替换源码，私钥在源码目录外。
- 包级核对 198 个锁定 PDF.js 资源（主脚本、worker、CMaps、字体、WASM），逐项匹配 tar 内容、release-manifest 和 vendor-lock 哈希；解包后本地 Flask 静态入口全部返回原字节，不从 CDN 读取；越界路径返回 404。
- 实测源码基准 `99b9034d35408b30af7148d54fa6af86c8f29f96`；测试包 SHA256 `d84b1895d6a8cf35f0d3a13d0173d253f20e103bddfc820260ed944cb376101d`。该包使用测试信任锚，已随临时目录清除，不能作为正式发布物。
- 首次联网构建因 Bootstrap Icons 下载完整性校验失败而停止；独立核对官方元数据与下载 SHA512 一致后重跑通过，未关闭校验。

没有修改 PDF 预览失败后的下载行为，没有生成客户端安装包或部署服务器。真机扫码与打印仍需独立验收。

## 同步上游 #63

合入上游 `dceb3e2` 的限频、限分辨率扫码及自适应标签布局，保留字体、字宽、布局和尺寸记忆；在这些设置下继续使用同一份 PDF 预览及下载。合并后 JavaScript 141 项、标签及扫码相关 Python 23 项通过，Chromium / WebKit 的完整 `assets_serialized` 均通过，覆盖相册真实二维码解码、扫码失败重试、A4 多页、20×12 mm 小标签、字体设置、认证资源读取和预览下载字节一致。

合并验收先发现未准备 PDF.js 资源导致动态导入失败，恢复锁定资源后继续；又发现合并时相册回调遗漏请求世代变量，已修正并完成上述重跑，未削弱断言或延长超时。
