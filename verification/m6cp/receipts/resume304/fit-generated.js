await openApp('Sheet'); await sleep(1500)
await page.getByRole('tab',{name:'Injection',exact:true}).click(); await sleep(1500)
const before = await api('/v1/rack/query?resource=scorer_console&as_of=now')
const started = new Date().toISOString()
const response = await fetch(`${BASE}/v1/rack/compaction-backtest`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({preview:true})})
const body = await response.text()
let result; try { result=JSON.parse(body) } catch { result=body }
const completed = new Date().toISOString()
const accepted = response.ok && result.verdict==='preview_only' && result.id && result.fit?.status==='proposed' && result.joint_fit?.creation_improved && result.joint_fit?.proposal_eligible && result.joint_fit?.size_tuning_active && result.joint_fit?.resized_dispositions>0 && result.joint_fit?.resized_boundaries>0
let uiError = null
try {
  const region = frame('injection_console').getByRole('region',{name:'Memory creation outcomes'})
  const summary = region.getByText('Compaction instruction generations',{exact:true})
  if ((await summary.locator('..').getAttribute('open'))===null) await summary.click()
  await sleep(4000)
  if (result.id) await region.getByRole('heading',{name:new RegExp(result.id)}).scrollIntoViewIfNeeded()
  else await region.scrollIntoViewIfNeeded()
} catch (error) { uiError=String(error) }
const screenshot = await shot(`fit-${started.replaceAll(':','-')}-${accepted?'accepted':'failed'}`)
const after = await api('/v1/rack/query?resource=scorer_console&as_of=now')
return {started,completed,httpStatus:response.status,accepted:Boolean(accepted && before.data.active_version===after.data.active_version),result,beforeActive:before.data.active_version,afterActive:after.data.active_version,screenshot,uiError}
