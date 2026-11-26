# 本地公共资源

Bootstrap 5.3.3 和 Bootstrap Icons 1.11.3，MIT 许可随各目录保存。来源及校验值唯一基准为 `clients/frontend/vendor-lock.json`（npm 官方发布包）。本目录仅复制该锁文件中的两包公共文件；服务器通过白名单 `/vendor/` 提供，客户端使用既有 `dist/vendor`，不重复打包。更新时同步版本锁、复制文件及 CHECKSUMS.sha256，并运行资源和客户端构建回归。
