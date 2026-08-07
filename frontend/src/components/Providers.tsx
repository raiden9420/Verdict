"use client";

import { PorscheDesignSystemProvider } from '@porsche-design-system/components-react';

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <PorscheDesignSystemProvider theme="light">
      {children}
    </PorscheDesignSystemProvider>
  );
}
