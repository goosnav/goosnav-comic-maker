/* Real browser regression suite for the two-panel fixture in scripts/verify.py.
   Run with the Codex cua_repl tab and browser viewport capability. The fixture
   must already be imported and exported in an isolated application state.
   Uses only visible DOM values, real input, and browser screenshots. */
export default async function testPanelEditor(tab, viewport) {
  const passed=[];
  const check=(value,name)=>{if(!value)throw Error(name);passed.push(name);};
  const p=tab.playwright, click=id=>p.locator('#'+id).click();
  const resultEdit=p.locator('#jobs .batch').filter({hasText:'fixture.png'}).locator('[data-job-edit]');
  const box=()=>p.evaluate(()=>Object.fromEntries(['x','y','w','h'].map(k=>[k,Number(document.querySelector('#box-'+k).value)])));
  const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  const bounds=selector=>p.locator(selector).evaluate(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};});
  const aligned=async name=>check(same(await bounds('#editor-image'),await bounds('#editor-overlay')),name);
  const observe=()=>tab.getAXState({emit:false});
  await resultEdit.click();await observe();
  await click('restore-panels');await click('fit-page');await observe();
  check(await p.locator('.panel-entry').count()===2,'Restore automatic detection');
  await aligned('Image and overlay alignment');
  const original=await box();
  let r=await bounds('#editor-overlay [data-index="0"][data-part="body"]');
  await tab.drag([r.x+r.w/2,r.y+r.h/2],[r.x+r.w/2+20,r.y+r.h/2+10]);await observe();
  const moved=await box();check(moved.x>original.x&&moved.y>original.y&&moved.w===original.w,'Move existing panel');
  await click('undo');await observe();check(same(await box(),original),'Undo move');
  await click('redo');await observe();check(same(await box(),moved),'Redo move');await click('undo');
  for(const part of ['nw','n','ne','e','se','s','sw','w']) {
    r=await bounds('#editor-overlay [data-part="'+part+'"]');
    const dx=part.includes('w')?-6:part.includes('e')?6:0,dy=part.includes('n')?-6:part.includes('s')?6:0;
    await tab.drag([r.x+r.w/2,r.y+r.h/2],[r.x+r.w/2+dx,r.y+r.h/2+dy]);await observe();
    const resized=await box();check(resized.w!==original.w||resized.h!==original.h,'Resize '+part);await click('undo');
  }
  await click('native-size');await observe();check(await p.locator('#zoom-label').innerText()==='100%','Native zoom');
  await click('pan-tool');r=await bounds('#editor-viewport');
  await tab.drag([r.x+r.w/2,r.y+r.h/2],[r.x+r.w/2-65,r.y+r.h/2-45]);await observe();
  check(await p.locator('#editor-viewport').evaluate(el=>el.scrollLeft>0||el.scrollTop>0),'Pan');await aligned('Alignment after pan');
  await click('fit-page');await click('add-tool');r=await bounds('#editor-overlay');
  await tab.drag([r.x+8,r.y+8],[r.x+32,r.y+32]);await observe();
  check(await p.locator('.panel-entry').count()===3,'Explicit Add panel');
  await p.locator('#editor-viewport').press('Delete');await observe();check(await p.locator('.panel-entry').count()===2,'Delete with keyboard');
  await click('undo');await observe();check(await p.locator('.panel-entry').count()===3,'Undo delete');
  await click('restore-panels');await p.getByRole('button',{name:'Panel 1',exact:true}).click();
  await p.getByRole('button',{name:'Move panel 1 later',exact:true}).click();await observe();
  check(same(await box(),original),'Reading order retains selected geometry');await click('undo');
  await p.locator('#editor-viewport').press('ArrowRight');await p.locator('#editor-viewport').press('Shift+ArrowDown');await observe();
  let n=await box();check(n.x===original.x+1&&n.y===original.y+10,'Source pixel keyboard nudges');await click('undo');await click('undo');
  await click('fit-panel');await observe();check(await p.locator('#zoom-label').innerText()!=='100%','Zoom to selected');
  await click('fit-page');await viewport.set({width:1100,height:780});await observe();await aligned('Alignment after window resize');
  await click('native-size');r=await bounds('#editor-viewport');await tab.scroll([r.x+r.w/2,r.y+r.h/2],'down',1);await observe();
  check(await p.locator('#editor-viewport').evaluate(el=>el.scrollTop>0),'Scrolled image');await aligned('Alignment after scroll');
  await click('fit-page');await viewport.reset();await observe();
  await p.locator('#box-x').fill(String(original.x+4));await p.locator('#box-x').press('Tab');await observe();
  check(Number(await p.locator('#editor-overlay [data-index="0"][data-part="body"]').getAttribute('x'))===original.x+4,'Numeric edit updates overlay before saving');
  await click('undo');
  await p.locator('#box-x').fill(String(original.x+8));await click('save-boundaries');await observe();
  check((await box()).x===original.x+8,'Save visible numeric coordinates');
  await click('editor-cancel');await resultEdit.click();await observe();
  check((await box()).x===original.x+8,'Reopen saved boundaries');
  await p.locator('#box-x').fill(String(original.x+28));await click('editor-cancel');await resultEdit.click();await observe();
  check((await box()).x===original.x+8,'Cancel discards draft');
  await p.getByRole('button',{name:'Panel 2',exact:true}).click();await click('delete-panel');
  await p.locator('#box-w').fill('465');await click('save-regenerate');await observe();
  await p.locator('#jobs [data-stop]').waitFor({state:'hidden',timeoutMs:30000});
  await resultEdit.click();await observe();
  check(await p.locator('.panel-entry').count()===1&&(await box()).w===465,'Regenerate and reopen corrected result');
  await p.locator('#crop-preview[src]').waitFor({state:'visible',timeoutMs:30000});
  check(await p.locator('#crop-preview').getAttribute('src')!==null,'Actual crop preview');
  return passed;
}
