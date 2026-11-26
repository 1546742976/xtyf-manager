# PR #56 冲突整合验证（2026-10-25）

将上游 `c6db089e2d6ae71987441843e8fff169de2f0af3`（#55）合入
`codex/audit-reliability-fixes`，第一父提交为 `ec91d58`。未改写已有提交。

## 合并结果

- 保留上游批量操作入口、跨页最多 100 种物料选择、退出后保留选择及原类别权限。
- `static/asset-labels.js` 的冲突段保留实际 PDF 获取、分页渲染、认证资源读取及同一字节保存。
  上游批量选中的完整列表交给该实现，不恢复简化画布或前 12 张限制；每次只渲染当前 PDF 页。
- 使用说明同时描述批量打印与完整 PDF 分页；清理旧预览说明的无引用 CSS。
- 更新上游浏览器断言：验证真实生成请求中的完整 ID、全部 PDF 身份与中文、分页及保存字节。
  客户端夹具使用原生认证读取/保存模拟，静态 PDF.js CMap 在 worker 加载，不放宽业务请求来源限制。

## 本次实际运行

| 检查 | 环境与结果 |
|---|---|
| `node tests/browser/assets_catalog_paging.cjs` | Windows、Node 22、Playwright 1.57.0；Chromium/WebKit 网站及模拟客户端四种环境通过；包含跨页 25 张热敏标签、中文与全部身份、原生保存字节一致、类别整理和分页 |
| `node tests/browser/assets_serialized.cjs` | Windows Chromium/WebKit 通过；保留单件借还、扫码、A4 多页、热敏尺寸、实际 PDF 下载一致及迟到请求隔离 |
| `python -m unittest test_asset_catalog_paging test_asset_pdf_resources -v` | Windows Python 3.11，`PYTHONPATH=.;tests`，8 项通过 |
| `python clients/frontend/test_frontend.py` | Windows Python 3.11，7 项通过；27 模板/291 资源构建 |
| `node --test tests/js/*.test.cjs` | 140 项通过 |
| `XTYF_UPGRADE_BASE_SHA=c6db089e2d6ae71987441843e8fff169de2f0af3 python tests/upgrade_from_main.py` | 隔离 Linux Python 3.13；基准工作区 HEAD 校对一致，1 项通过 |
| 修改脚本 `node --check`、`git diff --check` | 通过 |

新增测试首次发现夹具问题：空选择时点击已禁用的清空按钮；通过业务 fetch 读取 Blob 被客户端正确拒绝；
内嵌页面 `about:srcdoc` 不能作为 URL 基址；测试解码器未像产品一样在 worker 读取静态 CMap 导致中文提取失败。
逐项修正夹具后通过，保留失败日志，没有删除断言、延长超时或修改产品请求限制。

全部测试使用合成数据与独立临时服务，不修改手工验收站点。本次未重跑全站 Python 覆盖率或全部 23 组浏览器；
之前的全量结果见 [可靠性验收](remaining-reliability-2026-10-25.md)，不将其冒充合并后的全量结果。
原有 Linux 偶发会话失效和依赖公告仍待后续处理。模拟客户端不代表原生设备或真实打印机验收。

## 回滚

`git revert -m 1 <本文所在合并提交>` 可撤销本次上游整合，保留第一父提交的可靠性修复与业务数据。
这也会撤销本次引入的批量操作能力；不执行数据删除、部署或安装包重建。
