const { app, BrowserWindow, Menu, ipcMain, dialog, shell } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const defaults = require('./config.json');
const { normalizeServer } = require('./url.cjs');
let mainWindow, settingsWindow, serverUrl;
const settingsFile = () => path.join(app.getPath('userData'), 'server.json');
const settingsUrl = pathToFileURL(path.join(__dirname, 'settings.html')).href;
if (process.env.XTYF_SMOKE_DIR) app.setPath('userData', process.env.XTYF_SMOKE_DIR);

function readServer() {
  try { return normalizeServer(JSON.parse(fs.readFileSync(settingsFile(), 'utf8')).serverUrl); }
  catch { return normalizeServer(defaults.serverUrl); }
}
function openExternal(url) {
  if (/^https?:\/\//i.test(url)) shell.openExternal(url);
}
function showSettings() {
  if (settingsWindow) { settingsWindow.focus(); return; }
  settingsWindow = new BrowserWindow({ width: 600, height: 400, resizable: false,
    parent: mainWindow, modal: true, title: '服务器设置', autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true } });
  settingsWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  settingsWindow.webContents.on('will-navigate', e => e.preventDefault());
  settingsWindow.on('closed', () => { settingsWindow = null; });
  settingsWindow.loadFile('settings.html');
}
function trustedSettings(event) {
  return settingsWindow && event.sender === settingsWindow.webContents && event.senderFrame === settingsWindow.webContents.mainFrame && event.senderFrame.url === settingsUrl;
}
ipcMain.handle('settings:read', event => {
  if (!trustedSettings(event)) throw new Error('Invalid settings origin');
  return serverUrl;
});
ipcMain.handle('settings:save', async (event, input) => {
  if (!trustedSettings(event)) throw new Error('Invalid settings origin');
  const value = normalizeServer(input);
  fs.mkdirSync(path.dirname(settingsFile()), { recursive: true });
  fs.writeFileSync(settingsFile(), JSON.stringify({ serverUrl: value }, null, 2));
  serverUrl = value;
  settingsWindow.close();
  await mainWindow.loadURL(serverUrl).catch(() => {});
});

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  serverUrl = readServer();
  mainWindow = new BrowserWindow({ width: 1380, height: 900, minWidth: 800, minHeight: 600, show: !process.env.XTYF_SMOKE_DIR,
    title: defaults.name, backgroundColor: '#f5f7fb',
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true } });
  mainWindow.removeMenu();
  const contents = mainWindow.webContents;
  contents.session.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  contents.session.setPermissionCheckHandler(() => false);
  const navigate = (event, url) => {
    if (new URL(url).origin !== new URL(serverUrl).origin) { event.preventDefault(); openExternal(url); }
  };
  contents.on('will-navigate', navigate);
  contents.on('will-redirect', navigate);
  contents.setWindowOpenHandler(({ url }) => {
    if (new URL(url).origin === new URL(serverUrl).origin) contents.loadURL(url).catch(() => {});
    else openExternal(url);
    return { action: 'deny' };
  });
  contents.session.on('will-download', (_event, item) => {
    item.setSaveDialogOptions({ title: '保存文件', defaultPath: path.join(app.getPath('downloads'), path.basename(item.getFilename())) });
    item.once('done', (_e, state) => { if (state === 'interrupted') dialog.showErrorBox('下载失败', '文件未能保存，请重试。'); });
  });
  contents.on('did-fail-load', (_event, code, description, url, isMainFrame) => {
    if (!isMainFrame || code === -3 || process.env.XTYF_SMOKE_DIR) return;
    dialog.showMessageBox(mainWindow, { type: 'error', title: '连接失败',
      message: '无法连接服务器', detail: `${serverUrl}\n${description}\n请检查网络或修改服务器地址。`,
      buttons: ['重试', '服务器设置', '关闭'], defaultId: 0 }).then(({ response }) => {
        if (response === 0) mainWindow.loadURL(serverUrl).catch(() => {});
        if (response === 1) showSettings();
      });
  });
  mainWindow.loadURL(serverUrl).catch(() => {});
  if (process.env.XTYF_SMOKE_DIR) {
    const finish = async () => {
      const result = await contents.executeJavaScript('({title:document.title,url:location.href,inputs:document.querySelectorAll("input").length,nodeAvailable:typeof require!=="undefined"})');
      result.version = app.getVersion();
      result.applicationMenu = Menu.getApplicationMenu()?.items.map(item => item.label) ?? null;
      result.menuBarVisible = mainWindow.isMenuBarVisible();
      contents.sendInputEvent({ type: 'keyDown', keyCode: 'Alt' });
      contents.sendInputEvent({ type: 'keyUp', keyCode: 'Alt' });
      await new Promise(resolve => setTimeout(resolve, 200));
      result.menuBarVisibleAfterAlt = mainWindow.isMenuBarVisible();
      fs.writeFileSync(path.join(process.env.XTYF_SMOKE_DIR, 'result.json'), JSON.stringify(result, null, 2));
      const screenshot = await contents.capturePage();
      fs.writeFileSync(path.join(process.env.XTYF_SMOKE_DIR, 'window.png'), screenshot.toPNG());
      app.exit(result.applicationMenu === null && !result.menuBarVisible && !result.menuBarVisibleAfterAlt ? 0 : 4);
    };
    contents.once('did-finish-load', () => setTimeout(() => finish().catch(() => app.exit(2)), 1500));
    setTimeout(() => app.exit(3), 45000).unref();
  }
});
app.on('window-all-closed', () => app.quit());
