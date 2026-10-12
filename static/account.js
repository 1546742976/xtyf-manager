"use strict";
document.addEventListener('DOMContentLoaded', function(){
  const form=document.getElementById('account-password-form');
  const result=document.getElementById('account-result');
  if(!form)return;
  form.addEventListener('submit',async function(event){
    event.preventDefault();
    const values=new FormData(form);
    if(values.get('new')!==values.get('confirm')){
      result.textContent='两次输入的新密码不一致';
      result.classList.add('error');return;
    }
    const button=form.querySelector('[type=submit]');
    button.disabled=true;
    result.textContent='正在保存…';
    result.classList.remove('error');
    try{
      const response=await fetch('/api/password',{method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({old:values.get('old'),new:values.get('new')})});
      const data=await response.json();
      if(!response.ok||!data.ok)throw new Error(data.error||'修改失败');
      form.reset();result.textContent='密码已更新，请妥善保存新密码';
    }catch(error){
      result.classList.add('error');
      result.textContent=error.message||'网络异常，请重试';
    }finally{button.disabled=false;}
  });
});
