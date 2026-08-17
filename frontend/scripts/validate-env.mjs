import fs from "node:fs";
import path from "node:path";

function loadLocalEnv(filename) {
  const target = path.join(process.cwd(), filename);
  if (!fs.existsSync(target)) return;
  for (const rawLine of fs.readFileSync(target, "utf8").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const separator = line.indexOf("=");
    if (separator < 1) continue;
    const key = line.slice(0, separator).trim();
    let value = line.slice(separator + 1).trim();
    if (
      value.length >= 2 &&
      ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'")))
    ) {
      value = value.slice(1, -1);
    }
    if (process.env[key] === undefined) process.env[key] = value;
  }
}

loadLocalEnv(".env.local");
loadLocalEnv(".env");

const required = [
  "NEXT_PUBLIC_SUPABASE_URL",
  "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
];
const missing = required.filter((key) => !process.env[key]?.trim());
if (missing.length) {
  console.error(`Missing required frontend environment variables: ${missing.join(", ")}`);
  process.exit(1);
}

let supabaseUrl;
try {
  supabaseUrl = new URL(process.env.NEXT_PUBLIC_SUPABASE_URL.trim());
} catch {
  console.error("NEXT_PUBLIC_SUPABASE_URL must be a valid URL.");
  process.exit(1);
}
if (
  supabaseUrl.protocol !== "https:" &&
  !["localhost", "127.0.0.1"].includes(supabaseUrl.hostname)
) {
  console.error("NEXT_PUBLIC_SUPABASE_URL must use HTTPS outside local development.");
  process.exit(1);
}

const publishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY.trim();
if (publishableKey.startsWith("sb_secret_")) {
  console.error("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY must never be a secret/service key.");
  process.exit(1);
}

if (publishableKey.split(".").length === 3) {
  try {
    const encoded = publishableKey.split(".")[1]
      .replace(/-/g, "+")
      .replace(/_/g, "/");
    const payload = JSON.parse(Buffer.from(encoded, "base64").toString("utf8"));
    if (payload.role && payload.role !== "anon") {
      console.error(
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY is not an anon/publishable key.",
      );
      process.exit(1);
    }
  } catch {
    console.error("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY is a malformed JWT key.");
    process.exit(1);
  }
}

console.log("Frontend environment is configured for Supabase Auth.");
