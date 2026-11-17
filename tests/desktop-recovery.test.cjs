'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createRecovery}=require('../desktop/recovery.cjs');
function fixture(overrides={}){
  const calls=[];
  const ensure=createRecovery({probe:async()=>{calls.push('probe');return false;},stop:async()=>calls.push('stop'),start:async()=>{calls.push('start');return 'http://127.0.0.1:1234';},onReady:async origin=>calls.push(origin),delay:async()=>{},isClosing:()=>false,...overrides});
  return {calls,ensure};
}
test('concurrent reopen/wake events share one restart',async()=>{
  const {calls,ensure}=fixture();await Promise.all([ensure(),ensure(),ensure()]);
  assert.deepEqual(calls,['probe','probe','probe','stop','start','http://127.0.0.1:1234']);
});
test('healthy or briefly unavailable backend is retained',async()=>{
  let attempts=0;const {calls,ensure}=fixture({probe:async()=>++attempts===2});
  assert.equal(await ensure(),false);assert.deepEqual(calls,[]);
});
test('quit during recovery never starts a replacement',async()=>{
  let closing=false;const {calls,ensure}=fixture({isClosing:()=>closing,stop:async()=>{closing=true;}});
  assert.equal(await ensure(),false);assert.equal(calls.includes('start'),false);
});
test('failure clears singleflight and repeated crashes stop retrying',async()=>{
  const {ensure}=fixture({start:async()=>{throw new Error('failed');}});
  for(let i=0;i<3;i++)await assert.rejects(ensure(),/failed/);
  await assert.rejects(ensure(),/repeatedly stopped/);
});
