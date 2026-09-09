import assert from "node:assert/strict";
import test from "node:test";
import fixture from "../src/data/example-review.json";
import { arrayOf, consumeEventStream, isDebrief, isFinalReport, isMessageEvent, isTurn, isTurnsResponse, isVerdict, parseEventLines, validatedEventData } from "../src/lib/audit-stream";

test("saved example contracts and legacy turns pass; malformed nested evidence fails", () => {
  assert.ok(arrayOf(isTurn)(fixture.stream.turns));
  assert.ok(arrayOf(isVerdict)(fixture.stream.verdicts));
  assert.ok(arrayOf(isDebrief)(fixture.stream.debriefs));
  assert.ok(isFinalReport(fixture.stream.finalReport));
  assert.ok(isTurn({id:"legacy", exchange_number:1, sequence:0, agent_type:"attacker",content:{claim_summary:"Claim"}}));
  for (const content of [{critique_text:{}}, {cited_chunk_ids:[null]}, {attacker_validations:[{valid:true,chunk_text:{}}]}, {external_citations:[{title:42}]}, {provenance:[]}, {adjudication:{guard:{}}}]) {
    assert.equal(isTurn({...fixture.stream.turns[0],content}), false);
  }
  assert.equal(isTurnsResponse({status:"completed",turns:[null],verdicts:[]}), false);
  assert.equal(isMessageEvent({message:{private:"data"}}), false);
  assert.throws(() => validatedEventData('null',isTurn));
  assert.throws(() => validatedEventData('{',isTurn));
});

test("SSE parser handles multiline data, comments and invalid event IDs", () => {
  assert.deepEqual(parseEventLines([": heartbeat"]),null);
  assert.deepEqual(parseEventLines(["event: turn","id: 12","data: first","data: second"]), {type:"turn",id:"12",data:"first\nsecond"});
  assert.equal(parseEventLines(["id: bad\0value","data: ok"])?.id,undefined);
});

test("stream decoding preserves split UTF-8 characters, CRLF and final frames", async () => {
  const bytes = new TextEncoder().encode('event: turn\r\nid: 1\r\ndata: {"text":"café 科学"}\r\n\r\nevent: complete\ndata: {}');
  let index=0;
  const response=new Response(new ReadableStream({pull(controller){if(index===bytes.length) controller.close();else controller.enqueue(bytes.slice(index,index+=1));}}));
  const events: unknown[]=[];
  await consumeEventStream(response,event=>events.push(event));
  assert.deepEqual(events,[{type:"turn",id:"1",data:'{"text":"café 科学"}'},{type:"complete",id:undefined,data:"{}"}]);
});

test("oversized or invalid live events cancel the reader so recovery can take over",async()=>{
  for(const body of ['data: '+ 'a'.repeat(1_000_001),'event: turn\ndata: null\n\n']){
    let cancelled=false;
    const response=new Response(new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode(body));},cancel(){cancelled=true;}}));
    await assert.rejects(consumeEventStream(response,event=>{validatedEventData(event.data,isTurn);}));
    assert.equal(cancelled,true);
    assert.equal(response.body?.locked,false);
  }
});
