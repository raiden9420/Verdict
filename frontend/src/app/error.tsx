"use client";

import Link from "next/link";
import { Button } from "@/components/ui";

export default function ErrorPage({ retry }: { retry: () => void }) {
  return <main className="recovery-page"><span className="section-kicker">Workspace interrupted</span><h1>Let’s reopen your review.</h1><p>The page could not be displayed. Your saved papers and completed findings remain in your library.</p><div className="inline-actions"><Button onClick={retry}>Try again</Button><Link className="text-link" href="/?view=library">Return to library</Link></div></main>;
}
