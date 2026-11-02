import type { ReactNode } from "react";

import { useInitiatives } from "../../data/initiatives";
import { tokens } from "../../styles/tokens";

function Notice({ tone, title, children }: { tone: "info" | "danger"; title: string; children?: ReactNode }) {
  return (
    <div
      role="status"
      style={{
        background: tokens.color[tone].surface,
        borderColor: tokens.color[tone].border,
        padding: tokens.space[3],
      }}
    >
      <strong style={{ font: tokens.type.label }}>{title}</strong>
      {children}
    </div>
  );
}

export function InitiativesPage() {
  const { initiatives, error } = useInitiatives();
  return (
    <main style={{ padding: tokens.space[5] }}>
      <h1 style={{ font: tokens.type.heading }}>Initiatives</h1>
      {error && (
        <Notice tone="danger" title="Initiatives could not load">
          {error.message}
        </Notice>
      )}
      <ul>
        {initiatives.map((initiative) => (
          <li key={initiative.id}>{initiative.name}</li>
        ))}
      </ul>
    </main>
  );
}
