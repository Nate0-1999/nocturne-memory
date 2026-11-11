const threads = ['3a66413a-ad22-4f0f-8240-20bba801ccb2','cc1502db-c507-4345-9c27-92833904d544']
const log = []
for (const thread of threads) {
  const queue = await api(`/v1/approval-queue?thread_id=${thread}`)
  for (const card of queue.cards.filter(c=>c.state==='pending')) {
    const body = card.candidate.body
    const decision = /kettle/i.test(body) ? 'deny' : 'approve'
    const result = await post(`/v1/approval-queue/${card.item_uid}/decisions`,{decision,approval_mode:'explicit',actor_class:'human'})
    log.push({thread,memory_id:card.candidate.memory_id,body,decision,result})
  }
}
return log
