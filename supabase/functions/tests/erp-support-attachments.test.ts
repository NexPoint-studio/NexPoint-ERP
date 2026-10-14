import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { recoveryHmacSha256Hex as hmac, recoverySha256Hex as hash } from '../_shared/erp-admin-recovery-contract.ts';
import { ATTACHMENT_REQUEST_DOMAIN, canonicalAttachmentBody, validateImage, validateAttachmentOutput } from '../_shared/erp-support-attachment-contract.ts';
import { handleErpSupportAttachments } from '../erp-support-attachments/handler.ts';
const image=Buffer.from('89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de0000000c49444154089963606060000000040001f61738550000000049454e44ae426082','hex');
const sha=Buffer.from(await crypto.subtle.digest('SHA-256',image)).toString('hex');
const secret='q'.repeat(43),tenant='qa-support',installation='qa-support-001',now=1791072000;
const nonce='11111111-1111-4111-8111-111111111111',rid='22222222-2222-4222-8222-222222222222';
const body={schema_version:1,tenant_id:tenant,installation_id:installation,action:'upload',ticket_id:'qa-ticket',attachment_id:rid,name:'screen.png',content_type:'image/png',sha256:sha,data_base64:image.toString('base64')};
const env={get:(name:string)=>({SUPABASE_URL:'https://example.supabase.co',SUPABASE_SERVICE_ROLE_KEY:'QA_CANARY_SERVER'})[name]};
async function request(value=body){const text=JSON.stringify(value);return new Request('https://example.supabase.co/functions/v1/erp-support-attachments',{
method:'POST',body:text,headers:{'content-type':'application/json',authorization:`Bearer ${secret}`,'x-nexpoint-tenant-id':tenant,
'x-nexpoint-installation-id':installation,'x-nexpoint-timestamp':String(now),'x-nexpoint-nonce':nonce,'x-request-id':rid,
'x-nexpoint-signature':`v1=${await hmac(secret,[ATTACHMENT_REQUEST_DOMAIN,tenant,installation,String(now),nonce,rid,await hash(text)].join('\n'))}`}});}
const metadata={id:rid,name:body.name,content_type:body.content_type,sha256:sha,size_bytes:image.length,data_base64:'',created_at:'2026-10-14T00:00:00Z'};
test('attachment upload HMAC scope and projection',async()=>{
 const response=await handleErpSupportAttachments(await request(),env,async(input,init)=>{
 assert.equal(String(input),'https://example.supabase.co/rest/v1/rpc/np_erp_support_attachment');
 const args=JSON.parse(String(init?.body));assert.equal(args.p_secret_hash,await hash(secret));assert.deepEqual(args.p_input,body);
 return new Response(JSON.stringify({ok:true,attachments:[{...metadata,internal:'CANARY'}]}),{headers:{'content-type':'application/json'}});},now);
 assert.equal(response.status,200);assert.deepEqual((await response.json()).attachments,[metadata]);
});
for(const name of ['../evil.png','folder/screen.png','CON.png','screen.png.exe','.hidden.png',' screen.png'])test(`reject unsafe name ${name}`,()=>{
 assert.throws(()=>validateImage(name,'image/png',sha,body.data_base64));
});
test('reject mime mismatch, header mismatch, malformed image, size limit',()=>{
 assert.throws(()=>validateImage('screen.png','image/jpeg',sha,body.data_base64));
 assert.throws(()=>validateImage('screen.png','image/png',sha,Buffer.from('<script>evil</script>').toString('base64')));
 assert.throws(()=>validateImage('screen.png','image/png',sha,image.subarray(0,30).toString('base64')));
 assert.throws(()=>validateImage('screen.png','image/png',sha,Buffer.alloc(1048577).toString('base64')));
});
test('download integrity and size verified',async()=>{
 const value={...metadata,data_base64:body.data_base64};assert.deepEqual(await validateAttachmentOutput([value],true),[value]);
 await assert.rejects(()=>validateAttachmentOutput([{...value,sha256:'a'.repeat(64)}],true));
 await assert.rejects(()=>validateAttachmentOutput([{...value,size_bytes:1}],true));
 await assert.rejects(()=>validateAttachmentOutput([value],false));
});
test('list/download cannot carry upload data or spoof identity',()=>{
 assert.throws(()=>canonicalAttachmentBody({...body,tenant_id:'other'},tenant,installation));
 assert.throws(()=>canonicalAttachmentBody({...body,action:'list'},tenant,installation));
});
