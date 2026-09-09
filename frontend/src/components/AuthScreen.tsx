"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useAuth } from "@/components/AuthProvider";
import { Brand, Button, Icon } from "@/components/ui";

type AuthMode = "login" | "signup" | "reset";

function authErrorMessage(error: unknown): string {
  if (error instanceof TypeError) return "The sign-in service could not be reached. Check your connection and try again.";
  return error instanceof Error && error.message ? error.message : "We couldn’t complete sign-in. Please try again.";
}

export function AuthScreen() {
  const { configurationError, signIn, signUp, requestPasswordReset } = useAuth();
  const [mode, setMode] = useState<AuthMode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const changeMode = (next: AuthMode) => { setMode(next); setPassword(""); setError(null); setMessage(null); };

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submittingRef.current || configurationError) return;
    submittingRef.current = true;
    setSubmitting(true); setError(null); setMessage(null);
    try {
      if (mode === "login") await signIn(email.trim(), password);
      else if (mode === "reset") {
        await requestPasswordReset(email.trim());
        setMessage("If an account uses this address, you’ll receive a password reset link. Open it in this browser to choose a new password.");
      } else {
        const result = await signUp(email.trim(), password);
        if (result.requiresEmailConfirmation) setMessage("Check your email to confirm your account, then return here to sign in.");
      }
    } catch (error) { setError(authErrorMessage(error)); }
    finally { submittingRef.current = false; setSubmitting(false); }
  };

  return <main className="auth-page">
    <a className="skip-link" href="#auth-title">Skip to sign in</a>
    <header className="auth-topbar"><Link href="/" aria-label="Verdict home"><Brand /></Link><div className="inline-actions"><Link className="text-link" href="/example">Explore an example <Icon name="arrow-right" /></Link><ThemeToggle /></div></header>
    <div className="auth-layout">
      <section className="auth-story" aria-labelledby="product-title">
        <span className="section-kicker">A second reading. A stronger paper.</span>
        <h1 id="product-title">Make the case.<br /><em>Test the evidence.</em></h1>
        <p className="auth-lead">A structured, adversarial review of your research paper. See what holds up, what needs work, and which questions remain open.</p>
        <ol className="method-steps">
          <li><span>01</span><div><strong>A focused challenge</strong><p>A critic tests a specific claim, assumption, or research decision.</p></div></li>
          <li><span>02</span><div><strong>A defense from the paper</strong><p>The response must cite your manuscript. Its source passages are checked.</p></div></li>
          <li><span>03</span><div><strong>A finding you can inspect</strong><p>An adjudicator weighs both sides. Follow the evidence into a revision brief.</p></div></li>
        </ol>
        <Link className="example-callout" href="/example"><Icon name="book" /><span><strong>Read a worked example</strong><small>Explore three findings and their source evidence. No account needed.</small></span><Icon name="arrow-right" /></Link>
        <p className="method-footnote">AI-assisted scrutiny for human review. The roles may use the same model; disagreement is not independent verification or a measure of scientific truth.</p>
      </section>
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-card-heading"><span className="section-kicker">Your research workspace</span><h2 id="auth-title">{mode === "reset" ? "Reset your password" : mode === "signup" ? "Create an account" : "Sign in to Verdict"}</h2><p>{mode === "reset" ? "We’ll email you a link to get back in." : "Save your papers, inspect reviews, and compare revisions."}</p></div>
        {mode !== "reset" && <div className="auth-mode-switch" aria-label="Account action"><button type="button" aria-pressed={mode === "login"} className={mode === "login" ? "active" : ""} onClick={() => changeMode("login")} disabled={submitting}>Sign in</button><button type="button" aria-pressed={mode === "signup"} className={mode === "signup" ? "active" : ""} onClick={() => changeMode("signup")} disabled={submitting}>Create account</button></div>}
        <form className="auth-form" onSubmit={submit} aria-busy={submitting}>
          <label><span>Email address</span><input type="email" value={email} onChange={event => setEmail(event.target.value)} autoComplete="email" inputMode="email" required disabled={submitting || Boolean(configurationError)} placeholder="you@university.edu" /></label>
          {mode !== "reset" && <label htmlFor="auth-password"><span className="label-with-action"><span id="password-label">Password</span><button type="button" onClick={() => setShowPassword(!showPassword)} aria-label={showPassword ? "Hide password" : "Show password"} aria-pressed={showPassword}>{showPassword ? "Hide" : "Show"}</button></span><input id="auth-password" aria-labelledby="password-label" type={showPassword ? "text" : "password"} value={password} onChange={event => setPassword(event.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={mode === "signup" ? 8 : undefined} required disabled={submitting || Boolean(configurationError)} />{mode === "signup" && <small>Use at least 8 characters. A unique passphrase works well.</small>}</label>}
          {configurationError && <div className="auth-notice error" role="alert"><Icon name="information" /><span>Account access isn’t configured in this local copy. You can still explore the example. <details><summary>Setup details</summary>{configurationError}</details></span></div>}
          {error && <div className="auth-notice error" role="alert"><Icon name="error-filled" /><span>{error}</span></div>}
          {message && <div className="auth-notice success" role="status"><Icon name="check" /><span>{message}</span></div>}
          <Button type="submit" icon="arrow-right" loading={submitting} disabled={Boolean(configurationError)}>{mode === "reset" ? "Send reset link" : mode === "login" ? "Sign in" : "Create account"}</Button>
          <button className="text-link auth-reset" type="button" disabled={submitting} onClick={() => changeMode(mode === "reset" ? "login" : "reset")}>{mode === "reset" ? "Back to sign in" : "Forgot your password?"}</button>
        </form>
        <div className="auth-privacy"><Icon name="lock" /><p>Your library is private to your account. During a review, manuscript text is processed by external AI providers.</p></div>
      </section>
    </div>
    <footer className="auth-footer"><span>Built for a careful reading.</span><span>Text-based PDFs · Up to 20 MB · Up to 40 pages</span></footer>
  </main>;
}

export function PasswordRecoveryScreen() {
  const { updatePassword, signOut } = useAuth();
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const guard = useRef(false);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (guard.current) return;
    if (password !== confirmation) { setError("The passwords don’t match."); return; }
    guard.current = true; setBusy(true); setError(null);
    try { await updatePassword(password); }
    catch (error) { setError(authErrorMessage(error)); }
    finally { guard.current = false; setBusy(false); }
  };
  return <main className="recovery-page"><Brand /><h1>Choose a new password.</h1><form className="auth-form" onSubmit={submit}><label>New password<input type="password" autoComplete="new-password" value={password} minLength={8} required onChange={e => setPassword(e.target.value)} disabled={busy} /></label><label>Confirm new password<input type="password" autoComplete="new-password" value={confirmation} minLength={8} required onChange={e => setConfirmation(e.target.value)} disabled={busy} /></label>{error && <p role="alert" className="form-error">{error}</p>}<Button type="submit" loading={busy}>Save password and open workspace</Button><button className="text-link" type="button" disabled={busy} onClick={() => { void signOut().catch(error => setError(authErrorMessage(error))); }}>Cancel and sign out</button></form></main>;
}

export function AuthLoadingScreen() {
  return <main className="auth-page"><div className="workspace-loading" role="status"><span className="loading-mark" aria-hidden="true" /><strong>Opening your workspace</strong><span>Restoring your sign-in session.</span></div></main>;
}
