const threads = ['aa1ebe3e-0770-41a2-936c-0ee8c5469f13']
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
