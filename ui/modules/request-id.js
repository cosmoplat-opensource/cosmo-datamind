/* 请求编号:写操作的幂等键。crypto.randomUUID 只在 HTTPS 或 localhost 下存在,
   经内网 IP 用 HTTP 打开时改用 getRandomValues 生成同格式的 v4 UUID。 */
(function(global){
 'use strict';
 function fallback(){
  const c=global.crypto,b=new Uint8Array(16);
  if(c&&typeof c.getRandomValues==='function')c.getRandomValues(b);
  else for(let i=0;i<16;i++)b[i]=Math.floor(Math.random()*256);
  b[6]=(b[6]&0x0f)|0x40;b[8]=(b[8]&0x3f)|0x80;
  const h=[...b].map(x=>x.toString(16).padStart(2,'0')).join('');
  return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);
 }
 global.dmRequestId=function(){
  const c=global.crypto;
  return c&&typeof c.randomUUID==='function'?c.randomUUID():fallback();
 };
})(typeof window!=='undefined'?window:globalThis);
