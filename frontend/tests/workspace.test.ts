import { test } from "node:test";
import assert from "node:assert/strict";
import { readWorkspaceLocation, workspaceUrl } from "../src/lib/workspace-navigation";
import { buildRoundPlan, roundMatches } from "../src/lib/audit-workspace";
import { topicSelectionError } from "../src/lib/audit-config";
import fixture from "../src/data/example-review.json";
import type { ActiveAudit } from "../src/lib/audit-workspace";
import type { Turn, Verdict, DebriefCard } from "../src/types";

test("workspace URL only accepts known views and canonical-looking audit IDs", () => {
  const id = fixture.audit.auditId;
  assert.deepEqual(readWorkspaceLocation(`?view=report&audit=${id}`), { view: "report", auditId: id });
  assert.deepEqual(readWorkspaceLocation("?view=javascript:alert(1)&audit=garbage"), { view: null, auditId: null });
  assert.equal(workspaceUrl("library", id), "/?view=library");
  assert.equal(workspaceUrl("report", id), `/?view=report&audit=${id}`);
});

test("round recovery does not mix same-number exchanges from different topics", () => {
  const audit = { ...fixture.audit, roundTopics: ["experimental_setup", "statistical_rigor"], roundIds: [] } as ActiveAudit;
  const turns = fixture.stream.turns as Turn[];
  const plan = buildRoundPlan(audit, turns, fixture.stream.verdicts as Verdict[], fixture.stream.debriefs as DebriefCard[], null);
  assert.equal(plan.length, 2);
  assert.equal(plan[0].id, fixture.audit.roundIds[0]);
  assert.ok(roundMatches(turns[0], plan[0], 2));
  assert.equal(roundMatches(turns[0], plan[1], 2), false);
  assert.equal(roundMatches({}, plan[0], 2), false);
});

test("coverage limits reject duplicate and unknown topics", () => {
  assert.equal(topicSelectionError("fast", ["experimental_setup"]), null);
  assert.ok(topicSelectionError("fast", ["experimental_setup", "experimental_setup"]));
  assert.ok(topicSelectionError("deep", ["experimental_setup"]));
  assert.ok(topicSelectionError("fast", ["not-a-topic"]));
});
