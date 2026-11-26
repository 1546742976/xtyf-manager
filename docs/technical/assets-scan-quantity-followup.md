# 扫码数量与资产编辑验收

未知商品扫码建档原先只保存目录，丢失了标签数量。现在 `POST /api/assets/scan/create-receive` 在同一事务中完成建档、供应商映射、可选校区创建和入库来源记录。已知商品、未知耗材、未知非耗材均以有效标签数量预填实收数量，非法数量留空；只有用户确认才写入库存。请求仍经过登录、CSRF、权限和请求标识检查。

耗材及旧数量台账的物品编辑器增加校区库存选项，使用当前库存版本检查；物品资料与库存修改一起提交或回滚。已有单件通过具体设备管理数量与校区。管理员填写原因可更正单位名称，原始目录前后值记录在流水中；不改变类型转换、借单及单件身份约束。

## 本次实际验证

- Python 3.13：`python -B -m unittest discover -s tests -p 'test_asset*.py' -v`，47/47 通过。新增 4 项覆盖两种扫码建档入库、幂等重试、失败全回滚、权限、库存版本冲突和单件数量接口保护。
- `ruff check modules/assets.py modules/asset_scan.py tests/test_asset_editor.py` 通过。
- `node --test tests/js/asset_codes.test.cjs`，3/3 通过；`node --check static/assets.js`、`node --check static/asset-scan.js` 通过。
- Node 22 / Playwright 1.57.0：`node tests/browser/assets_serialized.cjs` 的 Chromium 和 WebKit 手机场景均通过。新增验证已知包装默认数量、新建耗材与非耗材默认数量、直接新增校区、非法数量空值、编辑校区库存；保留原有单件、打印、相册解码、网络重试与盘点检查。
- `node tests/browser/assets.cjs` 的 6 组原有资产与客户端模拟场景通过。以上使用隔离数据库、合成标签；没有写入用户库存或发送真实邮件，未进行实体相机、扫码枪及打印机验收。
- 本地验收服务仍使用 `http://127.0.0.1:60397/assets`；更新前已备份验收库并执行 SQLite 完整性检查，保留既有录入和会话密钥。预览数据、截图及备份均在忽略目录，不进入提交。

## Diff 与回滚

用本次提交的 `git show --stat <提交号>` 和 `git show <提交号>` 查看实际差异；使用 `git revert <提交号>` 回滚源码。新增入库仍使用原有业务表，回滚源码不删除已经保存的目录、单件或来源记录；不恢复数据库快照来撤销业务。

本次不推送、不部署、不打包、不递增版本。

## 2026-10-22 入口与弹窗调整

- 首页由 9 个入口缩为 7 个：扫码登记收进“新增物品”，与手动填写并列；普通成员仅可扫码登记已有耗材，手动目录维护仍受权限控制。
- “我的借用”保留原未还借单与分页，并内嵌本人单件、待接收及原有操作；归还后重新读取两个区域。原独立单件入口移除。
- 弹窗采用 22px 圆角及内部滚动。完整点击外部遮罩才退出，从弹窗内部拖到外面不关闭；提交期间不关闭，退出扫码仍释放相机。取消登记不提交库存。
- Node 24.17.0、Playwright 1.57.0、Python 3.13：`node tests/browser/assets_serialized.cjs` 的 Chromium／WebKit 两组通过，覆盖入口、内外点击、拖拽、焦点返回、统一借用页面及归还；`node tests/browser/assets.cjs` 六组通过，包含普通成员权限及客户端桥接模拟。测试使用临时数据库和合成数据。
- `node --check static/assets.js`、`node --check static/asset-units.js`、`node --check static/asset-scan.js` 及 `git diff --check` 通过。本批只改界面、相关验收和说明，不改数据库或后端。
- 本地原预览库继续使用，浏览器确认物品库存仍为 20。实际手机／扫码枪硬件未验收；没有推送、部署、打包或变更版本。

可用 `git show <本批提交号>` 查看差异，使用 `git revert <本批提交号>` 回滚本批界面改动；不回滚用户数据。
