const result = await post('/v1/rack/compaction-backtest',{memory_size:64,preview:false})
return {at:new Date().toISOString(),authenticEligible:result.fit.eligible_dispositions,result}
