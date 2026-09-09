import assert from "node:assert/strict";
import test from "node:test";
import { citationStatus, evidenceForExchange, evidenceForTurn, matchExternalValidation, outcomeCounts, safeExternalUrl, selectDisplayTurns, signedUrlRefreshDelay } from "../src/lib/audit-evidence";
import type { Turn, Verdict } from "../src/types";

function turn(id: string, role: Turn["agent_type"], sequence: number, content: Turn["content"] = {}, round = "round-a"): Turn {
  return { id, agent_type: role, sequence, exchange_number: 1, round_id: round, content };
}

test("evidence is matched to the accepted argument, correct role, exchange and round", () => {
  const attacker = turn("a", "attacker", 1, { cited_chunk_ids: ["chunk-a"] });
  const defender = turn("d", "defender", 2, { cited_chunk_ids: ["chunk-d"] });
  const validator = turn("v", "validator", 3, {
    attacker_validations: [{ chunk_id: "chunk-a", valid: true, similarity_score: .8, page_number: 2, chunk_text: "Attacker passage" }, { chunk_id: "unrelated", valid: true, similarity_score: .8, page_number: 12 }],
    defender_validations: [{ chunk_id: "chunk-d", valid: false, similarity_score: .1, page_number: 3, chunk_text: "Defense passage" }],
  });
  const differentRound = turn("other", "validator", 2, { attacker_validations: [{ chunk_id: "chunk-a", valid: true, similarity_score: .9, page_number: 99 }] }, "round-b");
  const turns = [differentRound, attacker, defender, validator];
  assert.deepEqual(evidenceForTurn(attacker, turns).map((item) => item.page_number), [2]);
  assert.deepEqual(evidenceForExchange(turns, 1).map((item) => [item.citedBy, item.page_number, item.valid]), [["Defense", 3, false], ["Challenge", 2, true]]);
  assert.deepEqual(evidenceForExchange(turns, 2), []);
});

test("required-evidence failures remain visible without a fabricated passage", () => {
  const defender = turn("d", "defender", 2, { cited_chunk_ids: [] });
  const validator = turn("v", "validator", 3, { defender_validations: [{ chunk_id: null, valid: false, similarity_score: 0, reason: "missing_citations" }] });
  const passages = evidenceForTurn(defender, [defender, validator]);
  assert.equal(passages.length, 1);
  assert.equal(passages[0].valid, false);
  assert.equal(passages[0].chunk_text, undefined);
});

test("transcript chooses latest accepted argument and never reuses an earlier defense", () => {
  const old = turn("old-a", "attacker", 1);
  const oldDefense = turn("old-d", "defender", 2);
  const accepted = turn("a", "attacker", 3);
  const referee = turn("r", "referee", 5);
  assert.deepEqual(selectDisplayTurns([old, oldDefense, accepted, referee]).map((item) => item.id), ["a", "r"]);
});

test("external validation matches canonical Unicode titles before positional metadata", () => {
  const a = { title: "Unrelated title", citation_index: 0, valid: false, reason: "citation_not_found" };
  const b = { title: "Étude – α", citation_index: 1, valid: true };
  assert.equal(matchExternalValidation([a, b], "ÉTUDE α", 0), b);
  assert.equal(matchExternalValidation([a], "Different citation", 0), undefined);
  assert.equal(matchExternalValidation([{ title: "", citation_index: 0, valid: true }], "Legacy citation", 0)?.valid, true);
});

test("citation explanations separate negative evidence, unavailable checks, and actual scope", () => {
  assert.equal(citationStatus({ title: "A", valid: false, reason: "existence_check_unavailable" }).label, "Check unavailable");
  assert.equal(citationStatus({ title: "A", valid: false, reason: "citation_not_found" }).label, "Not found");
  assert.equal(citationStatus({ title: "A", valid: false, reason: "topically_unrelated" }).label, "Relevance concern");
  assert.match(citationStatus({ title: "A", valid: true }).explanation, /specific in-text claim was not checked/);
  assert.equal(citationStatus().label, "Check pending");
});

test("external links allow only absolute web URLs without embedded credentials", () => {
  for (const value of ["javascript:alert(1)", "data:text/html,test", "file:///etc/passwd", "/relative", "https://user:pass@example.com", "not a URL"]) assert.equal(safeExternalUrl(value), null);
  assert.equal(safeExternalUrl("https://example.com/paper?id=1"), "https://example.com/paper?id=1");
});

test("outcome counts deduplicate exchange results and keep rounds independent", () => {
  const verdict = { id: "1", exchange_number: 1, verdict_type: "SOLIDIFIED", round_id: "r1" } as Verdict;
  assert.deepEqual(outcomeCounts([verdict, { ...verdict, id: "2", verdict_type: "CONTESTED" }, { ...verdict, id: "3", round_id: "r2" }, { ...verdict, exchange_number: 4 }]), { revision: 0, supported: 1, open: 1 });
});

test("signed URL refresh uses the returned lifetime with bounded malformed/short values", () => {
  assert.equal(signedUrlRefreshDelay(300), 240_000);
  assert.equal(signedUrlRefreshDelay(60), 30_000);
  assert.equal(signedUrlRefreshDelay(3), 1_000);
  assert.equal(signedUrlRefreshDelay(Number.NaN), 240_000);
  assert.equal(signedUrlRefreshDelay(-5), 240_000);
});
