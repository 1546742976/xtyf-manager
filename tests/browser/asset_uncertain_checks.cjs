'use strict';
const assert=require('node:assert/strict');
module.exports=async function({page,api,post,base,campus,label}){
  for(const [kind,body] of [['html','<html>upstream error</html>'],['truncated','{"ok":'],['shape','{"message":"success"}'],['null','null'],['server','{"ok":false,"error":"temporary"}']]){
    const item=(await post('/api/assets/catalog/items',{request_id:crypto.randomUUID(),name:`Synthetic uncertain ${label} ${kind}`,kind:'consumable',unit:'个'})).id;
    await page.goto(base+'/assets/detail?id='+item);
    await page.locator('#asset-status').filter({hasText:'数据已更新'}).waitFor();
    await page.locator(`[data-action=receive][data-id="${campus}"]`).click();
    await page.locator('#asset-form [name=quantity]').fill('10');
    await page.locator('#asset-form [name=reason]').fill('合成响应丢失测试');
    const requests=[];
    const handler=async route=>{
      requests.push(route.request().postDataJSON());
      const response=await route.fetch();assert.equal(response.status(),200);
      if(requests.length<3)await route.fulfill({status:kind==='server'?503:200,contentType:'application/json',body});
      else await route.fulfill({response});
    };
    await page.route('**/api/assets/operations',handler);
    await page.locator('#asset-save').click();
    await page.locator('#asset-form-status').filter({hasText:'结果尚未确认'}).waitFor();
    await page.locator('#asset-save').click();
    await page.waitForFunction(()=>!document.querySelector('#asset-save').disabled);
    await page.reload();await page.locator('#asset-pending-retry').waitFor();
    await page.locator('#asset-pending-retry').click();
    await page.locator('#asset-save').click();
    await page.locator('#asset-pending').waitFor({state:'hidden'});
    assert.equal(requests.length,3);assert.deepEqual(requests[0],requests[1]);assert.deepEqual(requests[0],requests[2]);
    const stock=(await(await api.get('/api/assets/items/'+item)).json()).campuses.find(c=>c.id===campus);
    assert.equal(stock.available,10);
    const events=(await(await api.get('/api/assets/events?item_id='+item)).json()).events;
    assert.equal(events.filter(e=>e.action==='receive').length,1);
    await page.unroute('**/api/assets/operations',handler);
  }
};
