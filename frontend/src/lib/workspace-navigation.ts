export type WorkspaceView = "setup" | "arena" | "report" | "library";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const views = new Set<WorkspaceView>(["setup", "arena", "report", "library"]);

export function readWorkspaceLocation(search: string): { view: WorkspaceView | null; auditId: string | null } {
  const params = new URLSearchParams(search);
  const view = params.get("view") as WorkspaceView;
  const audit = params.get("audit");
  return { view: views.has(view) ? view : null, auditId: audit && UUID.test(audit) ? audit : null };
}

export function workspaceUrl(view: WorkspaceView, auditId?: string | null): string {
  const params = new URLSearchParams({ view });
  if ((view === "arena" || view === "report") && auditId && UUID.test(auditId)) params.set("audit", auditId);
  return `/?${params.toString()}`;
}
