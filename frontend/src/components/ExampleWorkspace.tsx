"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AuditArena } from "./AuditArena";
import { FinalReportView } from "./FinalReportView";
import { ThemeToggle } from "./ThemeToggle";
import { Brand, Icon } from "./ui";
import type { ActiveAudit } from "@/lib/audit-workspace";
import type { UseSSEResult } from "@/hooks/useSSE";
import fixture from "@/data/example-review.json";

const audit = fixture.audit as ActiveAudit;
const stream = fixture.stream as UseSSEResult;

export function ExampleWorkspace() {
  const router = useRouter();
  const [view, setView] = useState<"arena" | "report">("arena");
  useEffect(() => {
    const update = () => setView(window.location.hash === "#report" ? "report" : "arena");
    update();
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  const navigate = (next: "arena" | "report") => {
    window.location.hash = next === "report" ? "report" : "findings";
    setView(next);
    window.requestAnimationFrame(() => { document.getElementById("example-main")?.focus({ preventScroll: true }); window.scrollTo({ top: 0 }); });
  };
  return <div><header className="auth-topbar"><Link href="/" aria-label="Verdict home"><Brand /></Link><div className="inline-actions"><Link className="text-link" href="/">Review your paper <Icon name="arrow-right" /></Link><ThemeToggle /></div></header><div className="example-banner"><span><strong>Worked example</strong> Fictional manuscript. Illustrative findings. No model was called.</span><span>Explore the evidence without an account.</span></div><main id="example-main" tabIndex={-1} className="example-content"><nav className="example-navigation" aria-label="Example review"><button type="button" aria-pressed={view === "arena"} onClick={() => navigate("arena")}>Findings &amp; evidence</button><button type="button" aria-pressed={view === "report"} onClick={() => navigate("report")}>Revision brief</button></nav>{view === "arena" ? <AuditArena audit={audit} stream={stream} source={fixture.source} onNewAudit={() => { router.push("/"); }} onOpenReport={() => navigate("report")} /> : <FinalReportView audit={audit} stream={stream} sample onOpenArena={() => navigate("arena")} onNewVersion={() => { router.push("/"); }} />}</main></div>;
}
