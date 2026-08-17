import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let browserClient: SupabaseClient | null = null;

export function getSupabaseConfigurationError(): string | null {
  if (!process.env.NEXT_PUBLIC_SUPABASE_URL?.trim()) {
    return "NEXT_PUBLIC_SUPABASE_URL is not configured.";
  }
  if (!process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?.trim()) {
    return "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY is not configured.";
  }
  return null;
}

export function getSupabaseBrowserClient(): SupabaseClient {
  const configurationError = getSupabaseConfigurationError();
  if (configurationError) {
    throw new Error(configurationError);
  }
  if (typeof window === "undefined") {
    throw new Error("The Supabase browser client is only available in the browser.");
  }

  if (!browserClient) {
    browserClient = createClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!.trim(),
      process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!.trim(),
      {
        auth: {
          autoRefreshToken: true,
          persistSession: true,
          detectSessionInUrl: true,
        },
      },
    );
  }

  return browserClient;
}
