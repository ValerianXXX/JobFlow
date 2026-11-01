"use strict";
const assert=require('node:assert/strict');
const {spawn,execFileSync}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const root=path.resolve(__dirname,'../../..');
const {chromium}=require('playwright');
const task=JSON.parse(fs.readFileSync(path.join(__dirname,'task-card.json'),'utf8'));
const out=path.join(root,'reports/material-review-native',process.env.JOBFLOW_OBSERVATION_ID||new Date().toISOString().replace(/[:.]/g,'-'));
fs.mkdirSync(out,{recursive:true});
assert.equal(fs.existsSync(path.join(out,'result.json')),false,'Preserve previous observations');
const git=(...args)=>execFileSync('git',args,{cwd:root,windowsHide:true,encoding:'utf8'}).trim();
const boundPaths=['src','scripts','config','tests/pdf_regions_ui_server.py','tests/first_use_revision_ui_server.py','tests/fixtures/first-use-revision-v1','tests/fixtures/fake_jobops_ai.py','tests/fixtures/cjk-column-response-v1.json','tests/fixtures/material-layout-probes-v1'];
assert.equal(git('diff','HEAD','--',...boundPaths),'','Commit runtime and fixture changes before recording an observation');
const report={protocol:task.protocol,synthetic_only:true,model:task.model,new_model_calls:0,independent_human:false,steps:[],
 reference_runtime:task.runtime_commit,observed_head:git('rev-parse','HEAD'),observed_product_tree:git('rev-parse','HEAD:src'),
 runtime_matches_reference:git('diff',task.runtime_commit,'--',...boundPaths)==='',
 started_at:new Date().toISOString(),harness_sha256:crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex')};
const save=()=>fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(report,null,2)+'\n');
const checkpoint=(step,detail)=>{report.steps.push({step,at:new Date().toISOString(),...detail});save();process.stdout.write(JSON.stringify(report.steps.at(-1))+'\n');};
let child,browser,page,stdout='',stderr='',exitPromise;
(async()=>{
 try{
  const python=process.env.JOBFLOW_TEST_PYTHON||path.join(root,'.venv/Scripts/python.exe');
  child=spawn(python,[path.join(root,'tests/first_use_revision_ui_server.py'),'--stdio-control','--artifacts',out],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
  exitPromise=new Promise(resolve=>child.once('exit',resolve));
  child.stdout.on('data',chunk=>{stdout+=chunk;});child.stderr.on('data',chunk=>{stderr+=chunk;});
  const url=await new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>reject(new Error('Fixture startup deadline')),120000);
   child.once('error',e=>{clearTimeout(timer);reject(e);});
   child.once('exit',code=>{clearTimeout(timer);reject(new Error('Fixture startup exit '+code));});
   child.stdout.on('data',()=>{if(stdout.includes('\n')){clearTimeout(timer);resolve(JSON.parse(stdout.split('\n')[0]).url);}});
  });
  browser=await chromium.launch({channel:'msedge',headless:true});
  page=await browser.newPage({viewport:{width:1365,height:900}});page.setDefaultTimeout(45000);
  const origin=new URL(url).origin,external=[],errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>{if(route.request().url().startsWith(origin+'/'))return route.continue();external.push(route.request().url());return route.abort();});
  const idle=()=>page.locator('#activityIndicator').waitFor({state:'hidden'});
  await page.goto(url);await page.waitForFunction(()=>state.data&&!document.querySelector('#documentFile').disabled);
  const initial=await page.evaluate(()=>({sources:state.data.sources.length,claims:state.data.claims.length,status:state.data.status}));
  assert.deepEqual(initial,task.expected_initial);checkpoint('empty_workspace',initial);
  await page.locator('#documentFile').setInputFiles(path.join(root,task.source));
  await page.locator('.source-preview').waitFor();await idle();
  assert.equal(await page.locator('.preview-select').count(),5);assert.equal(await page.locator('.preview-select:checked').count(),0);
  await page.locator('.source-review-open').click();await page.waitForFunction(()=>document.querySelector('.source-original img')?.naturalWidth>0);
  await page.locator('.source-original').screenshot({path:path.join(out,'original.png')});
  await page.locator('.visual-source-confirm').check();await page.locator('.include-all-preview').click();
  await page.waitForFunction(()=>state.data?.claims?.length===5);await idle();
  await page.reload();await page.waitForFunction(()=>state.data?.claims?.length===5);await idle();
  const retained=await page.evaluate(()=>({sources:state.data.sources.length,decisions:state.data.claims.map(x=>x.decision),status:state.data.status}));
  assert.deepEqual(retained.decisions,Array(5).fill('PENDING'));assert.equal(retained.status,'IN_PROGRESS');checkpoint('retained_unapproved_after_reload',retained);
  await page.locator('[data-locale="en"]').click();await idle();
  await page.locator('.step[data-target="questionnaire"]').click();
  for(const detail of await page.locator('#questionGroups details').all()){
   if(await detail.getAttribute('open')===null)await detail.locator(':scope > summary').click();
  }
  const fields=await page.evaluate(()=>state.data.catalog.fields.map(f=>({id:f.id,type:f.input_type})));
  assert.equal(fields.length,Object.keys(task.answers).length+task.decline.length+task.not_applicable.length);
  for(const field of fields){
   const row=page.locator(`[data-question="${field.id}"]`);
   if(Object.hasOwn(task.answers,field.id)){
    const input=row.locator('.answer-input');
    if(field.type==='select')await input.selectOption(task.answers[field.id]);else await input.fill(task.answers[field.id]);
    await row.locator('.status-select').selectOption('CONFIRMED');
   }else if(task.decline.includes(field.id)){
    await row.locator('.status-select').selectOption('PREFER_NOT_TO_ANSWER');
    await row.locator('.policy-select').selectOption('do_not_store');
   }else{
    assert.ok(task.not_applicable.includes(field.id));await row.locator('.status-select').selectOption('NOT_APPLICABLE');
   }
  }
  await page.locator('#saveAnswers').click();await idle();
  const saved=await page.evaluate(()=>({resolved:state.data.completion,answerCount:Object.keys(state.data.answers).length,status:state.data.status}));
  assert.equal(saved.status,'IN_PROGRESS');checkpoint('answers_saved_through_ui',saved);
  await page.locator('.step[data-target="finish"]').click();
  await page.locator('#finalConfirm').check();
  const prematureResponse=page.waitForResponse(response=>response.url().endsWith('/api/complete'));
  await page.locator('#completeOnboarding').click();
  const rejected=await prematureResponse;const rejectedBody=await rejected.json();await idle();
  assert.equal(rejected.status(),400);assert.equal(rejectedBody.code,'PROFILE_REVIEW_REQUIRED');
  assert.equal(await page.evaluate(()=>state.data.status),'IN_PROGRESS');
  checkpoint('premature_completion_blocked',{http:rejected.status(),code:rejectedBody.code,status:'IN_PROGRESS'});
  await page.locator('.step[data-target="review"]').click();
  for(const decision of await page.locator('.claim-decision').all())await decision.selectOption('CONFIRMED');
  await page.locator('#profileReview').check();await page.locator('#saveReview').click();await idle();
  const review=await page.evaluate(()=>({decisions:state.data.claims.map(x=>x.decision),profile:state.data.profile_review,status:state.data.status,readiness:state.data.readiness}));
  assert.deepEqual(review.decisions,Array(5).fill('CONFIRMED'));assert.equal(review.profile,'CONFIRMED');checkpoint('review_saved_through_ui',review);
  await page.locator('#finalConfirm').check();
  await page.locator('#completeOnboarding:not([disabled])').waitFor();await page.locator('#completeOnboarding').click();await idle();
  await page.waitForFunction(()=>state.data?.status==='ONBOARDING_COMPLETE');
  checkpoint('completed_from_empty',{status:await page.evaluate(()=>state.data.status)});
  await page.locator('#externalClaimConfirm').check();await page.locator('#approveExternalClaims:not([disabled])').click();await idle();
  await page.locator('#createStandardResume:not([disabled])').waitFor();
  const choices=page.locator('.standard-resume-choice');assert.equal(await choices.count(),5);
  for(let i=2;i<5;i++)await choices.nth(i).uncheck();
  await page.locator('#createStandardResume').click();await idle();await page.locator('#standardResumeDraft').waitFor();
  const preview=await page.evaluate(()=>state.standardResume.preview);
  assert.equal(preview.claim_count,2);assert.equal(preview.excluded_claim_count,3);
  const downloadPromise=page.waitForEvent('download');await page.locator('#downloadStandardResume').click();
  const download=await downloadPromise;await download.saveAs(path.join(out,'selected-from-empty.docx'));
  checkpoint('first_resume_from_empty',{selected:preview.claim_count,excluded:preview.excluded_claim_count,layout:preview.layout,wording:preview.sections.flatMap(x=>x.items.map(y=>y.text)),docx_sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(out,'selected-from-empty.docx'))).digest('hex')});
  await page.screenshot({path:path.join(out,'desktop-completed.png')});
  await page.reload();await page.locator('#restoreStandardResume:not([disabled])').waitFor();await page.locator('#restoreStandardResume').click();await idle();
  assert.equal(await page.locator('.standard-resume-choice:checked').count(),2);
  assert.equal(await page.locator('#standardResumeConfirm').isChecked(),false);
  await page.setViewportSize({width:390,height:844});await page.locator('#standardResumePanel').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth),true);
  await page.screenshot({path:path.join(out,'mobile-restored.png')});
  assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
  checkpoint('restored_without_consent',{selected:2,external_requests:external.length,page_errors:errors.length});

  await page.setViewportSize({width:1365,height:900});
  await page.locator('#previewStandardResumePdf').click();await idle();
  await page.waitForFunction(()=>state.standardResumePage?.page_count>0);
  const pageCount=await page.evaluate(()=>state.standardResumePage.page_count),pageImages=[];
  for(let index=1;index<=pageCount;index++){
   if(index>1){await page.locator('#standardResumeNext').click();await idle();}
   await page.waitForFunction(()=>document.querySelector('#standardResumePageImage')?.naturalWidth>0);
   const rendered=await page.evaluate(()=>state.standardResumePage);
   assert.equal(rendered.page,index);
   const bytes=Buffer.from(rendered.image_data_url.split(',')[1],'base64'),filename=`standard-page-${index}.png`;
   fs.writeFileSync(path.join(out,filename),bytes);
   pageImages.push({file:filename,sha256:crypto.createHash('sha256').update(bytes).digest('hex'),page:index});
  }
  const draftBinding=await page.evaluate(()=>({docx_sha256:state.standardResume.docx_sha256,provider:state.standardResume.pdf_provider,materials_validated:state.standardResume.materials_validated}));
  assert.equal(draftBinding.materials_validated,false);
  checkpoint('awaiting_actual_visual_review',{pages:pageImages,...draftBinding});
  const visualGate=path.join(out,'standard-visual-review.json'),deadline=Date.now()+15*60*1000;
  while(!fs.existsSync(visualGate)){
   if(Date.now()>deadline)throw new Error('Actual visual review not received; adoption not attempted');
   await new Promise(resolve=>setTimeout(resolve,1000));
  }
  const reviewRecord=JSON.parse(fs.readFileSync(visualGate,'utf8'));
  assert.equal(reviewRecord.synthetic_only,true);assert.equal(reviewRecord.reviewer,'ASSISTANT_VISUAL_REVIEW');
  assert.deepEqual(reviewRecord.pages.map(p=>({file:p.file,sha256:p.sha256,page:p.page})),pageImages);
  assert.ok(reviewRecord.pages.every(p=>p.result==='PASS'&&p.reason));
  checkpoint('actual_pages_inspected',{review_file:'standard-visual-review.json',pages:pageCount,independent_human:false});
  const previousRevision=await page.evaluate(()=>state.data.revision_number);
  await page.locator('#standardResumeConfirm').check();await page.locator('#adoptStandardResume').click();await idle();
  const revised=await page.evaluate(()=>({revision:state.data.revision_number,status:state.data.status,profile:state.data.profile_review,sources:state.data.sources.length,claims:state.data.claims.length,externalCurrent:state.data.external_claim_approval.current,tailoringCurrent:state.data.tailoring_manifest.current}));
  assert.equal(revised.status,'IN_PROGRESS');assert.equal(revised.revision,previousRevision+1);
  assert.equal(revised.profile,'PENDING');assert.equal(revised.sources,1);assert.equal(revised.claims,5);
  assert.equal(revised.externalCurrent,false);assert.equal(revised.tailoringCurrent,false);
  checkpoint('adopted_new_editable_revision',revised);
  await page.locator('.step[data-target="questionnaire"]').click();
  const salary=page.locator('[data-question="desired_salary"]');
  const salaryGroup=salary.locator('xpath=ancestor::details[1]');
  if(await salaryGroup.getAttribute('open')===null)await salaryGroup.locator(':scope > summary').click();
  await salary.locator('.answer-input').fill('115000');await salary.locator('.status-select').selectOption('CONFIRMED');
  await page.locator('#saveAnswers').click();await idle();
  await page.locator('.step[data-target="review"]').click();
  for(const decision of await page.locator('.claim-decision').all())await decision.selectOption('CONFIRMED');
  await page.locator('#profileReview').check();await page.locator('#saveReview').click();await idle();
  await page.locator('#finalConfirm').check();await page.locator('#completeOnboarding:not([disabled])').click();await idle();
  assert.equal(await page.evaluate(()=>state.data.status),'ONBOARDING_COMPLETE');
  assert.equal(await page.evaluate(()=>state.data.external_claim_approval.current),false);
  await page.locator('#externalClaimConfirm').check();await page.locator('#approveExternalClaims:not([disabled])').click();await idle();
  await page.locator('#openTailoringManifest').click();await idle();
  const proposal=await page.evaluate(()=>state.tailoringProposal);
  assert.ok(proposal.candidate_count>0);await page.locator('#selectRecommendedTailoring').click();
  assert.ok(await page.locator('.tailoring-select:checked').count()>0);
  await page.locator('#tailoringManifestConfirm').check();await page.locator('#approveTailoringManifest').click();await idle();
  const ready=await page.evaluate(()=>state.data.application_readiness);
  assert.equal(ready.status,'READY_FOR_OFFLINE_APPLICATION_PREPARATION');
  checkpoint('revision_completed_and_permissions_renewed',{revision:revised.revision,readiness:ready,tailoring_candidates:proposal.candidate_count});
  const advanced=page.locator('#advancedTools');if(await advanced.getAttribute('open')===null)await advanced.locator(':scope > summary').click();
  await page.locator('#applicationOfficialUrl').fill('https://example.com/careers/analyst-first-use');
  await page.locator('#applicationFormUrl').fill('https://example.com/careers/analyst-first-use/apply');
  await page.locator('#applicationGuestMode').selectOption('yes');
  for(const [id,file] of [['applicationJdFile','role.txt'],['applicationOfficialFile','official.html'],['applicationFormFile','form.html']]){
   await page.locator('#'+id).setInputFiles(path.join(root,task.application_fixtures,file));
  }
  await page.locator('#applicationEvidenceExcerpt').fill('Example Analytics Lab builds documented research workflows.');
  const preparedResponse=page.waitForResponse(response=>response.url().endsWith('/api/prepare-offline-application'),{timeout:180000});
  await page.locator('#prepareOfflineApplication:not([disabled])').click();
  const response=await preparedResponse,body=await response.json();
  checkpoint('offline_application_response',{http:response.status(),result:body});
  assert.equal(response.status(),200);await idle();
  await page.locator('#reviewPacketPanel').waitFor();
  const packet=await page.evaluate(()=>state.reviewPacket);
  fs.writeFileSync(path.join(out,'review-packet.json'),JSON.stringify(packet,null,2)+'\n');
  assert.equal(packet.status,'AWAITING_APPROVAL');assert.equal(packet.application_status,'AWAITING_APPROVAL');
  assert.equal(packet.packet.material_plan.all_uploads_and_submission_blocked,true);
  const selectedFacts=packet.packet.resume_bullets.map(item=>item.text);
  assert.equal(selectedFacts.length,2);assert.equal(new Set(selectedFacts).size,2);
  report.expected_final_facts=selectedFacts;
  await page.screenshot({path:path.join(out,'application-awaiting-approval.png')});
  const id=packet.application_id;await page.reload();await page.locator(`.open-review-packet[data-id="${id}"]`).waitFor();
  await page.locator(`.open-review-packet[data-id="${id}"]`).click();await idle();
  assert.equal(await page.evaluate(()=>state.reviewPacket.application_id),id);
  assert.equal(await page.evaluate(()=>state.reviewPacket.status),'AWAITING_APPROVAL');
  await page.setViewportSize({width:390,height:844});await page.locator('#reviewPacketPanel').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth),true);
  await page.screenshot({path:path.join(out,'mobile-restored-packet.png')});
  assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
  checkpoint('application_packet_restored_without_approval',{application_status:'AWAITING_APPROVAL',external_requests:external.length,page_errors:errors.length});

  await page.setViewportSize({width:1365,height:900});
  const files=page.locator('.application-material-review');
  await files.locator('[data-material-id="resume.pdf"] .material-preview').waitFor();
  await files.scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(out,'final-files-desktop-en.png')});
  const dialog=page.locator('.material-review-dialog');
  report.product_material_reviews=[];report.product_downloads=[];
  for(const purpose of ['resume','cover_letter']){
   const card=files.locator(`[data-material-id="${purpose}.pdf"]`);
   await card.locator('.material-preview').click();
   await dialog.locator('img').waitFor({state:'visible'});
   await page.waitForFunction(()=>document.querySelector('.material-review-dialog img')?.naturalWidth>0);
   assert.match(await dialog.locator('.material-page-status').innerText(),/Page 1 of 1/);
   const image=await dialog.locator('img').getAttribute('src');
   const imageBytes=Buffer.from(image.split(',')[1],'base64');
   fs.writeFileSync(path.join(out,`product-${purpose}-page-1.png`),imageBytes);
   await dialog.screenshot({path:path.join(out,`product-${purpose}-preview.png`)});
   report.product_material_reviews.push({purpose,page:1,page_count:1,png_sha256:crypto.createHash('sha256').update(imageBytes).digest('hex')});
   await page.keyboard.press('Escape');assert.equal(await dialog.locator('img').count(),0);
   for(const format of ['pdf','docx']){
    const card=files.locator(`[data-material-id="${purpose}.${format}"]`);
    const event=page.waitForEvent('download');await card.locator('.material-download').click();
    const download=await event,bytes=fs.readFileSync(await download.path());
    const expected=packet.packet.material_plan[purpose][`${format}_sha256`];
    assert.equal('sha256:'+crypto.createHash('sha256').update(bytes).digest('hex'),expected);
    const file=`downloaded-${purpose}.${format}`;fs.writeFileSync(path.join(out,file),bytes);
    report.product_downloads.push({purpose,format,file,sha256:expected,suggested_filename:download.suggestedFilename()});
   }
  }
  await page.locator('[data-locale="zh"]').click();await idle();
  await page.waitForFunction(()=>document.querySelector('.application-material-review')?.textContent.includes('检查最终申请材料'));
  await page.setViewportSize({width:390,height:844});
  await files.locator('[data-material-id="resume.pdf"] .material-preview').click();
  await dialog.locator('img').waitFor({state:'visible'});await dialog.locator('select').selectOption('200');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth),true);
  assert.equal(await dialog.evaluate(element=>element.scrollWidth<=element.clientWidth),true);
  await page.screenshot({path:path.join(out,'final-material-mobile-zh.png')});
  await dialog.locator('.material-close').click();assert.equal(await dialog.locator('img').count(),0);
  assert.equal(await page.evaluate(()=>state.reviewPacket.status),'AWAITING_APPROVAL');
  assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
  checkpoint('final_files_reviewed_and_downloaded_through_product',{pdfs:2,downloads:4,locales:['en','zh'],mobile_zoom:'200%',approval_unchanged:true,external_requests:external.length,page_errors:errors.length});

  report.status='PASS';report.completed_at=new Date().toISOString();

 }catch(error){
  report.status='FAILED';report.error=String(error);if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});fs.writeFileSync(path.join(out,'failure.txt'),String(error)+'\n'+await page.locator('body').innerText().catch(()=>''));}throw error;
 }finally{
  if(browser)await browser.close();
  if(child){child.stdin.end('stop\n');const result=await Promise.race([exitPromise,new Promise(resolve=>setTimeout(()=>resolve('timeout'),20000))]);
   report.fixture_exit=result;if(result==='timeout'){child.kill();report.cleanup_status='TIMEOUT';}else{
    report.server=JSON.parse(stdout.trim().split(/\r?\n/).at(-1));
    assert.equal(result,0);assert.equal(report.server.temporary_state_removed,true);
    assert.equal(report.server.real_model_calls,0);assert.equal(report.server.database_counts.external_action_attempts,0);assert.equal(report.server.database_counts.approvals,0);
   }
  }
  if(stderr)fs.writeFileSync(path.join(out,'server-stderr.txt'),stderr);
  report.bound_source_unchanged=git('diff',report.observed_head,'--',...boundPaths)==='';
  if(!report.bound_source_unchanged){report.status='FAILED';process.exitCode=1;}
  if(report.status==='PASS'){
   const python=process.env.JOBFLOW_TEST_PYTHON||path.join(root,'.venv/Scripts/python.exe');
   const program='import json,sys; from docx import Document; p=json.load(sys.stdin); paragraphs=[x.text for x in Document(p["file"]).paragraphs]; print(json.dumps([paragraphs.count(f) for f in p["facts"]]))';
   const counts=JSON.parse(execFileSync(python,['-c',program],{cwd:root,windowsHide:true,encoding:'utf8',input:JSON.stringify({file:path.join(out,'native-qa-1/resume.docx'),facts:report.expected_final_facts})}));
   assert.deepEqual(counts,[1,1]);report.final_docx_fact_occurrences=counts;
  }
  save();
 }
})().catch(error=>{report.status='FAILED';report.error=String(error);save();process.stderr.write(String(error)+'\n');process.exitCode=1;});
