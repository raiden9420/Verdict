"use client";

import { useState } from "react";
import { PButton, PIcon } from "@porsche-design-system/components-react";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useAuth } from "@/components/AuthProvider";

type AuthMode = "login" | "signup";

function authErrorMessage(error: unknown): string {
  if (error instanceof TypeError) {
    return "The authentication service could not be reached. Check your connection and try again.";
  }
  return error instanceof Error && error.message
    ? error.message
    : "Authentication failed. Please try again.";
}

export function AuthScreen() {
  const { configurationError, signIn, signUp } = useAuth();
  const [mode, setMode] = useState<AuthMode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const changeMode = (nextMode: AuthMode) => {
    setMode(nextMode);
    setError(null);
    setMessage(null);
  };

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting || configurationError) return;

    setSubmitting(true);
    setError(null);
    setMessage(null);
    try {
      if (mode === "login") {
        await signIn(email.trim(), password);
      } else {
        const result = await signUp(email.trim(), password);
        if (result.requiresEmailConfirmation) {
          setMessage("Check your email to confirm your account, then return here to sign in.");
        }
      }
    } catch (authError) {
      setError(authErrorMessage(authError));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="auth-page">
      <div className="auth-topbar">
        <div className="brand-lockup">
          <span className="verdict-wordmark">Verdict</span>
          <span className="brand-divider" />
          <span className="product-name">ADVERSARIAL AUDIT</span>
        </div>
        <ThemeToggle />
      </div>

      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-mark" aria-hidden="true"><PIcon name="document" /></div>
        <div className="section-kicker">RESEARCH INTEGRITY / ACCOUNT</div>
        <h1 id="auth-title">{mode === "login" ? "Welcome back." : "Create your workspace."}</h1>
        <p className="auth-intro">
          Sign in to keep papers, audits, and reports private to your account.
        </p>

        <div className="auth-mode-switch" role="tablist" aria-label="Account action">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "login"}
            className={mode === "login" ? "active" : ""}
            onClick={() => changeMode("login")}
            disabled={submitting}
          >
            Log in
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "signup"}
            className={mode === "signup" ? "active" : ""}
            onClick={() => changeMode("signup")}
            disabled={submitting}
          >
            Sign up
          </button>
        </div>

        <form className="auth-form" onSubmit={submit} aria-busy={submitting}>
          <label>
            <span>Email address</span>
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              inputMode="email"
              required
              disabled={submitting || Boolean(configurationError)}
            />
          </label>
          <label>
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              minLength={6}
              required
              disabled={submitting || Boolean(configurationError)}
            />
            {mode === "signup" && <small>Use at least 6 characters.</small>}
          </label>

          {(configurationError || error) && (
            <div className="auth-notice error" role="alert">
              <PIcon name="error-filled" />
              <span>{configurationError || error}</span>
            </div>
          )}
          {message && (
            <div className="auth-notice success" role="status">
              <PIcon name="check" />
              <span>{message}</span>
            </div>
          )}

          <PButton
            type="submit"
            icon="arrow-right"
            loading={submitting}
            disabled={submitting || Boolean(configurationError)}
          >
            {mode === "login" ? "Log in to Verdict" : "Create account"}
          </PButton>
        </form>
      </section>
    </main>
  );
}

export function AuthLoadingScreen() {
  return (
    <main className="auth-page">
      <div className="workspace-loading" role="status">
        <span className="loading-mark" aria-hidden="true" />
        <strong>Opening your workspace</strong>
        <span>Restoring your secure session.</span>
      </div>
    </main>
  );
}
