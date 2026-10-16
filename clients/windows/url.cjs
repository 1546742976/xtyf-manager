function normalizeServer(input) {
  const url = new URL(input.trim());
  if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password || url.search || url.hash || (url.pathname !== '/' && url.pathname !== '')) {
    throw new Error('请输入网站根地址，例如 http://manager.skywalker-rm.online（不含账号、密码、路径或查询参数）');
  }
  return url.origin;
}
module.exports = { normalizeServer };
