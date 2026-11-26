/* Build-time compiled Jinja: no eval, remote templates, or template fetch. */
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const get = (value, key) => value == null ? undefined : value[key];
  const safeJSON = value => JSON.stringify(value ?? null).replace(/</g,'\\u003c').replace(/>/g,'\\u003e').replace(/&/g,'\\u0026').replace(/'/g,'\\u0027').replace(/\u2028/g,'\\u2028').replace(/\u2029/g,'\\u2029');
  const urls = {index:'/',tasks:'/tasks',account:'/account',manage:'/manage',members:'/members',login:'/login',logout:'/logout',export_csv:'/export.csv',export_members_csv:'/api/members/export.csv','discussions.index':'/discussions/','security.security_page':'/admin/security','site_updates.timeline':'/updates','site_updates.version':'/api/site-release','site_updates.history':'/api/site-release/history'};
  function urlFor(name, options={}) {
    if(name==='static') return '/static/'+options.filename;
    if(!Object.hasOwn(urls,name)) throw new Error('Unknown local route: '+name);
    const query=new URLSearchParams(options).toString();
    return urls[name]+(query?'?'+query:'');
  }
  const templates={/* COMPILED_TEMPLATES */};
  function render(name,context={},blocks={}) {
    if(!Object.hasOwn(templates,name)) throw new Error('Unknown packaged template: '+name);
    return templates[name]({...context},blocks);
  }
  globalThis.XTYFTemplates={render,names:Object.keys(templates)};
})();
