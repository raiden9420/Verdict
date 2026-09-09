import Link from "next/link";

export default function NotFound() {
  return <main className="recovery-page"><span className="section-kicker">Page not found</span><h1>This page is unavailable.</h1><p>Open your workspace to find a saved paper or start a review.</p><Link className="button button-primary" href="/">Open Verdict</Link></main>;
}
