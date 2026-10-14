import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { recoveryHmacSha256Hex as hmac, recoverySha256Hex as hash } from '../_shared/erp-admin-recovery-contract.ts';
import { SUPPORT_REQUEST_DOMAIN, SUPPORT_RESPONSE_DOMAIN, canonicalSupportBody } from '../_shared/erp-support-contract.ts';
import { handleErpSupport } from '../erp-support/handler.ts';
const secret='q'.repeat(43),tenant='qa-support',installation='qa-support-001',now=1791072000;
const nonce='11111111-1111-4111-8111-111111111111',rid='22222222-2222-4222-8222-222222222222';
const context=[tenant,installation,String(now),nonce,rid];
const body={schema_version:1,tenant_id:tenant,installation_id:installation,action:'detail',ticket_id:'ticket-qa',operation_id:'',body:'',status:''};
const env={get:(name:string)=>({SUPABASE_URL:'https://example.supabase.co',SUPABASE_SERVICE_ROLE_KEY:'QA_CANARY_SERVER'})[name]};
async function request(value=body){const text=JSON.stringify(value);return new Request('https://example.supabase.co/functions/v1/erp-support',{
method:'POST',body:text,headers:{'content-type':'application/json',authorization:`Bearer ${secret}`,'x-nexpoint-tenant-id':tenant,
'x-nexpoint-installation-id':installation,'x-nexpoint-timestamp':String(now),'x-nexpoint-nonce':nonce,'x-request-id':rid,
'x-nexpoint-signature':`v1=${await hmac(secret,[SUPPORT_REQUEST_DOMAIN,...context,await hash(text)].join('\n'))}`}});}
const ticket={ticket_id:'ticket-qa',protocol:'QA',subject:'Example',description:'Text',category:'general',priority:'normal',status:'open',
opened_at:'2026-10-14T00:00:00Z',updated_at:'2026-10-14T00:00:00Z',messages:[],messages_truncated:false};
function json(value:unknown){return new Response(JSON.stringify(value),{headers:{'content-type':'application/json'}});}
test('credential scoped RPC and signed public projection strips internal fields',async()=>{
 const response=await handleErpSupport(await request(),env,async(input,init)=>{
 assert.equal(String(input),'https://example.supabase.co/rest/v1/rpc/np_erp_support');
 const args=JSON.parse(String(init?.body));assert.equal(args.p_secret_hash,await hash(secret));assert.equal(args.p_tenant_id,tenant);
 assert.equal(args.p_installation_id,installation);assert.deepEqual(args.p_input,body);
 return json({ok:true,tickets:[{...ticket,internal_notes:['PRIVATE_CANARY'],metadata:{secret:'PRIVATE_CANARY'}}],tickets_truncated:false});},now);
 assert.equal(response.status,200);const text=await response.text();assert.ok(!text.includes('PRIVATE_CANARY'));
 assert.ok(!text.includes(secret));assert.equal(response.headers.get('x-nexpoint-signature'),`v1=${await hmac(secret,[SUPPORT_RESPONSE_DOMAIN,...context,await hash(text)].join('\n'))}`);
});
for(const code of ['unauthorized','replay','rate_limited','not_found','conflict'])test(`RPC rejects ${code}`,async()=>{
 const response=await handleErpSupport(await request(),env,async()=>json({ok:false,code}),now);
 assert.equal(response.status,({unauthorized:401,replay:409,rate_limited:429,not_found:404,conflict:409})[code]);
});
test('rejects internal event even if RPC regresses',async()=>{
 const response=await handleErpSupport(await request(),env,async()=>json({ok:true,tickets:[{...ticket,messages:[{event_type:'internal_note',body:'PRIVATE'}]}],tickets_truncated:false}),now);
 assert.equal(response.status,502);assert.ok(!(await response.text()).includes('PRIVATE'));
});
test('spoofed tenant rejects before network',async()=>{
 let called=false;const response=await handleErpSupport(await request({...body,tenant_id:'other'}),env,async()=>{called=true;return json({});},now);
 assert.equal(response.status,403);assert.equal(called,false);
});
test('mutation requires stable UUID and bounded text',()=>{
 assert.throws(()=>canonicalSupportBody({...body,action:'reply',body:'hello'},tenant,installation));
 assert.throws(()=>canonicalSupportBody({...body,action:'reply',operation_id:rid,body:'a'.repeat(4001)},tenant,installation));
 assert.throws(()=>canonicalSupportBody({...body,action:'status',operation_id:rid,status:'admin'},tenant,installation));
 assert.equal(JSON.parse(canonicalSupportBody({...body,action:'reply',operation_id:rid,body:'hello'},tenant,installation)).body,'hello');
});
test('browser origin rejected',async()=>{const req=await request();req.headers.set('origin','https://evil.test');
 const response=await handleErpSupport(req,env,async()=>{throw Error('network must not run')},now);assert.equal(response.status,405);});

test('history and list pagination bounded decimal offset',()=>{
 assert.equal(JSON.parse(canonicalSupportBody({...body,action:'history',body:'200'},tenant,installation)).body,'200');
 assert.equal(JSON.parse(canonicalSupportBody({...body,action:'list',ticket_id:'',body:'100'},tenant,installation)).body,'100');
 for(const value of ['-1','01','100001','1e2','x']) assert.throws(()=>canonicalSupportBody({...body,action:'history',body:value},tenant,installation));
});
