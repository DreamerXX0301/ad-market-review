import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import ts from 'typescript';
const code = ts.transpileModule(await fs.readFile(new URL('./aiAttachments.ts', import.meta.url), 'utf8'), {compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022}}).outputText;
const {attachmentType,attachmentIssue,readAttachmentFiles,safeAttachmentUrl} = await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
let reads=0;
globalThis.FileReader=class {
  readAsDataURL(file){ reads++; this.result='data:'+file.type+';base64,aGVsbG8='; queueMicrotask(()=>this.onload()); }
};
const txt={name:'财报摘要.MD',type:'',size:5};
const provider={capabilities:{images:true,max_files:3,max_file_bytes:5242880,max_total_bytes:10485760}};
const items=await readAttachmentFiles([txt],[],provider);
assert.equal(items[0].input.media_type,'text/markdown');
assert.equal(items[0].input.data_base64,'aGVsbG8=');
assert.equal(items[0].input.name,txt.name);
assert.equal(attachmentType('K线.JPEG'),'image/jpeg');
assert.equal(attachmentType('quote.svg'),undefined);
const before=reads;
for (const files of [
 [{name:'bad.svg',size:7}], [{name:'bad.txt',size:0}], [{name:'big.png',size:5242881}],
 Array.from({length:4},()=>txt), Array.from({length:3},()=>({...txt,size:4000000})),
]) await assert.rejects(readAttachmentFiles(files,[],provider));
assert.equal(reads,before,'Invalid uploads are rejected before bytes are read');
const image=[{input:{media_type:'image/png'}}];
assert.match(attachmentIssue(image,{capabilities:{images:false}}),/不支持图片/);
assert.equal(attachmentIssue(image,provider),'');
assert.equal(attachmentIssue(items,{capabilities:{images:false}}),'');
assert.equal(safeAttachmentUrl('/api/v1/ai/sessions/ai-session_abc/messages/m_123/attachments/a-1'),'/api/v1/ai/sessions/ai-session_abc/messages/m_123/attachments/a-1');
for (const url of ['javascript:alert(1)','https://evil.test/a','//evil.test/a','/api/v1/ai/sessions/../messages/a/attachments/b','data:image/svg+xml;base64,x','/api/v1/ai/sessions/a/messages/b/attachments/c?evil=1']) assert.equal(safeAttachmentUrl(url),'');
console.log('AI attachment selection, model compatibility and safe links passed');
