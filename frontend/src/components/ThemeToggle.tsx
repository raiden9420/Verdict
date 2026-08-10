"use client";

import { useTheme } from "@/components/ThemeProvider";
import styles from "./ThemeToggle.module.css";

export type ThemeToggleProps = {
  className?: string;
  showLabel?: boolean;
};

export function ThemeToggle({
  className,
  showLabel = false,
}: ThemeToggleProps) {
  const { toggleTheme } = useTheme();
  const classes = className ? `${styles.toggle} ${className}` : styles.toggle;

  return (
    <button
      type="button"
      className={classes}
      onClick={toggleTheme}
      title="Toggle light and dark theme"
    >
      <span className={styles.icon} aria-hidden="true">
        <svg
          className={styles.moonIcon}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          focusable="false"
        >
          <path d="M20.5 14.3A8.7 8.7 0 0 1 9.7 3.5 8.7 8.7 0 1 0 20.5 14.3Z" />
        </svg>
        <svg
          className={styles.sunIcon}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          focusable="false"
        >
          <circle cx="12" cy="12" r="3.6" />
          <path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.65 17.65l1.42 1.42M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.65 6.35l1.42-1.42" />
        </svg>
      </span>
      {showLabel && (
        <span className={styles.label} aria-hidden="true">
          <span className={styles.darkActionLabel}>Dark</span>
          <span className={styles.lightActionLabel}>Light</span>
        </span>
      )}
      <span className={styles.assistiveLabel}>
        <span className={styles.lightThemeDescription}>Light theme active. Switch to dark theme.</span>
        <span className={styles.darkThemeDescription}>Dark theme active. Switch to light theme.</span>
      </span>
    </button>
  );
}

export default ThemeToggle;
