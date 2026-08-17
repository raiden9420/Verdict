"use client";

import { PorscheDesignSystemProvider } from '@porsche-design-system/components-react';
import { AuthProvider } from "@/components/AuthProvider";

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <PorscheDesignSystemProvider>
      <AuthProvider>{children}</AuthProvider>
    </PorscheDesignSystemProvider>
  );
}
